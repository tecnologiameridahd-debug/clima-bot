"""Dashboard web del bot WindBorne Monitor (Flask).

Corre en el propio servidor (sin restricciones de navegador), asi que sí
puede llamar en vivo a la API de WindBorne. Pensado para correr local con
`python webapp.py` y para desplegarse en Render con gunicorn (ver
Procfile / render.yaml).
"""
import io
import os
import threading

from flask import Flask, Response, jsonify, request

from analysis import analizar, analizar_todas, edge_kalshi, log_peak
from api import WindBorneError
from charts import grafico_completo, grafico_historial
from cities import KALSHI_CITIES, get_city, resolve_location
from config import TELEGRAM_TOKEN
from cyclones import BASINS, BASINS_USA, ciclones_activos
from fallback_om import resumen_om

app = Flask(__name__)

_bot_thread_started = False


def _maybe_start_bot():
    """Arranca el polling de Telegram (bot.py) en un hilo de fondo.

    Desactivado por defecto para no disparar el bot real al probar el
    dashboard local. En Render se activa con RUN_TELEGRAM_BOT=1 para que
    un solo servicio web (gratis) sirva el dashboard y el bot a la vez.
    Requiere --workers 1: dos procesos pollando el mismo token chocan
    contra Telegram (409 Conflict) y duplican respuestas.
    """
    global _bot_thread_started
    if _bot_thread_started:
        return
    if os.environ.get("RUN_TELEGRAM_BOT", "0") not in ("1", "true", "True"):
        return
    if not TELEGRAM_TOKEN:
        print("[webapp] RUN_TELEGRAM_BOT activo pero falta WB_TELEGRAM_TOKEN; bot no iniciado.")
        return
    import bot as bot_module

    threading.Thread(target=bot_module.main, daemon=True, name="telegram-bot").start()
    _bot_thread_started = True
    print("[webapp] Bot de Telegram iniciado en background thread.")


_maybe_start_bot()


def _error_json(e, status=502):
    return jsonify({"error": str(e)}), status


BUILD_VERSION = "v4.2-pico-dia"


@app.get("/health")
def health():
    return jsonify({"status": "ok", "build": BUILD_VERSION})


@app.get("/api/status")
def api_status_route():
    from api import api_status

    return jsonify(api_status())


@app.get("/api/ciudades")
def api_ciudades():
    return jsonify(
        [
            {"id": cid, "nombre": c["nombre"], "serie": c.get("serie"), "station": c.get("station")}
            for cid, c in KALSHI_CITIES.items()
        ]
    )


def _resolver_ciudad(city_id):
    try:
        return get_city(city_id)
    except ValueError:
        lugar = resolve_location(city_id)
        if lugar:
            return lugar
        raise


@app.get("/api/resumen/<city_id>")
def api_resumen(city_id):
    try:
        city = _resolver_ciudad(city_id)
    except ValueError:
        return jsonify({"error": f"Ciudad desconocida: {city_id}"}), 404

    try:
        a = analizar(city)
    except WindBorneError as e:
        # Fallback Open-Meteo si WindBorne no responde.
        try:
            om = resumen_om(city=city)
            return jsonify(
                {
                    "city": city["nombre"],
                    "fuente": "open-meteo",
                    "wb_error": str(e),
                    "ahora_f": om.get("ahora_f"),
                    "pico_f": om.get("pico_hoy"),
                    "min_f": om.get("min_hoy"),
                }
            )
        except Exception as e2:
            return _error_json(f"WindBorne: {e} · Open-Meteo: {e2}")

    if not a:
        return jsonify({"error": "Sin datos para hoy"}), 502

    log_peak(a)
    metar = a.get("metar")
    metar_max = a.get("metar_max_hoy")
    hw = a.get("pico_wm6_max_hoy")
    pk = a.get("pico_kalshi")
    return jsonify(
        {
            "city": city["nombre"],
            "fuente": "windborne",
            "fecha": a["fecha"],
            "init_txt": a["init_txt"],
            "ahora_f": a["ahora"]["temp_f"],
            "ahora_hora": a["ahora"]["hora"],
            "pico_f": a["pico"]["temp_f"],
            "pico_hora": a["pico"]["hora"],
            "pico_wm6_max_hoy": (
                {"temp_f": hw["temp_f"], "hora": hw.get("hora")}
                if hw and hw.get("temp_f") is not None
                else None
            ),
            "pico_kalshi": (
                {
                    "temp_f": pk["temp_f"],
                    "hora": pk.get("hora"),
                    "fuente": pk.get("fuente"),
                }
                if pk and pk.get("temp_f") is not None
                else None
            ),
            "min_f": a["min_dia"],
            "promedio": a["promedio"],
            "probs_pico": a["probs_pico"],
            "metar": (
                {"temp_f": metar["temp_f"], "age_min": metar["age_min"], "station": metar["station"]}
                if metar and metar.get("temp_f") is not None
                else None
            ),
            "metar_max_hoy": (
                {"temp_f": metar_max["temp_f"], "hora": metar_max["hora"], "station": metar_max["station"]}
                if metar_max and metar_max.get("temp_f") is not None
                else None
            ),
        }
    )


@app.get("/api/grafico/<city_id>.png")
def api_grafico(city_id):
    try:
        city = _resolver_ciudad(city_id)
    except ValueError:
        return jsonify({"error": f"Ciudad desconocida: {city_id}"}), 404
    try:
        a = analizar(city)
    except WindBorneError as e:
        return _error_json(e)
    if not a:
        return jsonify({"error": "Sin datos para graficar"}), 502
    img = grafico_completo(a)
    if not img:
        return jsonify({"error": "Sin datos para graficar"}), 502
    return Response(io.BytesIO(img).getvalue(), mimetype="image/png")


@app.get("/api/edge/<city_id>")
def api_edge(city_id):
    try:
        city = _resolver_ciudad(city_id)
    except ValueError:
        return jsonify({"error": f"Ciudad desconocida: {city_id}"}), 404
    try:
        a = analizar(city)
    except WindBorneError as e:
        return _error_json(e)
    if not a:
        return jsonify({"error": "Sin datos para hoy"}), 502

    return jsonify(
        {
            "city": city["nombre"],
            "pico_f": a["pico"]["temp_f"],
            "pico_hora": a["pico"]["hora"],
            "tips": edge_kalshi(a),
        }
    )


@app.get("/api/kalshi")
def api_kalshi():
    resultados, errores = analizar_todas()
    salida = []
    for a in resultados:
        c = a["city"]
        m = a.get("metar")
        hw = a.get("pico_wm6_max_hoy") or {}
        salida.append(
            {
                "id": c["id"],
                "nombre": c["nombre"],
                "serie": c.get("serie"),
                "pico_f": a["pico"]["temp_f"],
                "pico_hora": a["pico"]["hora"],
                "pico_max_hoy": hw.get("temp_f"),
                "prob97": a["probs_pico"].get(97, 0),
                "prob98": a["probs_pico"].get(98, 0),
                "metar_f": m["temp_f"] if m and m.get("temp_f") is not None else None,
            }
        )
    return jsonify({"ciudades": salida, "errores": errores})


@app.get("/api/historial/<city_id>.png")
def api_historial(city_id):
    img = grafico_historial(city_id)
    if not img:
        return jsonify({"error": "Sin historial todavía (hace falta más de una consulta guardada)"}), 404
    return Response(img, mimetype="image/png")


@app.get("/api/huracanes")
def api_huracanes():
    global_ = request.args.get("global") == "1"
    basins = list(BASINS.keys()) if global_ else BASINS_USA
    activos, errores = ciclones_activos(basins=basins)

    salida = []
    for storm_id, storm in activos.items():
        genesis = storm.get("genesis") or {}
        salida.append(
            {
                "id": storm_id,
                "nombre": storm.get("storm_name") or storm_id,
                "cuenca": storm.get("_basin"),
                "cuenca_nombre": BASINS.get(storm.get("_basin"), storm.get("_basin")),
                "lat": genesis.get("latitude"),
                "lon": genesis.get("longitude"),
                "wind_kt": storm.get("wind_kt"),
                "mslp_hpa": storm.get("mslp_hpa"),
                "start_time": storm.get("start_time"),
                "end_time": storm.get("end_time"),
            }
        )

    return jsonify({"activos": salida, "errores": errores, "global": global_})


DASHBOARD_HTML = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>WindBorne Monitor — Dashboard</title>
<style>
  :root {
    --bg: #eef3f2; --surface: #fff; --ink: #16222b; --ink-soft: #4d5f69;
    --line: #ccd8d9; --accent: #0d7a83; --accent-tint: #0d7a831a;
    --ok: #22794f; --ok-tint: #22794f1a; --warn: #a9660f; --warn-tint: #a9660f1a;
    --danger: #a3311f; --danger-tint: #a3311f1a; --code-bg: #e3ebea;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #0b141a; --surface: #101c22; --ink: #e7eef0; --ink-soft: #93aab1;
      --line: #22343a; --accent: #37bcc4; --accent-tint: #37bcc424;
      --ok: #4fbf85; --ok-tint: #4fbf8524; --warn: #e0a446; --warn-tint: #e0a44624;
      --danger: #e37a63; --danger-tint: #e37a6324; --code-bg: #0d1a1f;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--ink);
    font: 15px/1.5 -apple-system, "Segoe UI", Roboto, sans-serif;
  }
  header { padding: 20px 24px; border-bottom: 1px solid var(--line); background: var(--surface); }
  header h1 { margin: 0 0 4px; font-size: 20px; }
  header p { margin: 0; color: var(--ink-soft); font-size: 13px; }
  main { max-width: 900px; margin: 0 auto; padding: 24px; }
  .controls { display: flex; gap: 10px; align-items: center; margin-bottom: 20px; flex-wrap: wrap; }
  select, button {
    font: inherit; padding: 8px 12px; border-radius: 6px; border: 1px solid var(--line);
    background: var(--surface); color: var(--ink);
  }
  button { cursor: pointer; }
  button.tab { border-color: transparent; }
  button.tab.active { background: var(--accent); color: #fff; border-color: var(--accent); }
  .card {
    background: var(--surface); border: 1px solid var(--line); border-radius: 8px;
    padding: 18px 20px; margin-bottom: 16px;
  }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 14px; }
  .stat dt { font-size: 11px; text-transform: uppercase; letter-spacing: .05em; color: var(--ink-soft); margin: 0 0 4px; }
  .stat dd { margin: 0; font-size: 22px; font-weight: 600; font-variant-numeric: tabular-nums; }
  .hero-pico {
    display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between;
    gap: 12px; padding: 16px 18px; margin: 12px 0 16px; border-radius: 10px;
    background: linear-gradient(135deg, var(--accent-tint), transparent);
    border: 1px solid var(--accent);
  }
  .hero-pico .label { font-size: 12px; text-transform: uppercase; letter-spacing: .06em; color: var(--ink-soft); margin: 0 0 4px; }
  .hero-pico .value { font-size: 40px; font-weight: 700; font-variant-numeric: tabular-nums; color: var(--accent); line-height: 1.1; margin: 0; }
  .hero-pico .sub { margin: 4px 0 0; font-size: 13px; color: var(--ink-soft); }
  .pill { display: inline-block; font-size: 12px; padding: 3px 9px; border-radius: 4px; margin: 2px 4px 2px 0; }
  .pill.hi { background: var(--danger-tint); color: var(--danger); }
  .pill.mid { background: var(--warn-tint); color: var(--warn); }
  .pill.lo { background: var(--ok-tint); color: var(--ok); }
  img.grafico { width: 100%; border-radius: 6px; border: 1px solid var(--line); }
  .muted { color: var(--ink-soft); font-size: 13px; }
  .error { background: var(--danger-tint); color: var(--danger); padding: 12px 16px; border-radius: 6px; }
  .storm { border: 1px solid var(--line); border-radius: 6px; padding: 12px 16px; margin-bottom: 10px; }
  .storm h4 { margin: 0 0 6px; }
  .hidden { display: none; }
  #tab-huracanes label { display: flex; align-items: center; gap: 6px; font-size: 13px; margin-bottom: 12px; }
  .table-wrap { overflow-x: auto; }
  table.kalshi { width: 100%; border-collapse: collapse; font-size: 14px; }
  table.kalshi th {
    text-align: left; font-size: 11px; text-transform: uppercase; letter-spacing: .04em;
    color: var(--ink-soft); padding: 8px 10px; border-bottom: 1px solid var(--line);
  }
  table.kalshi td { padding: 9px 10px; border-bottom: 1px solid var(--line); font-variant-numeric: tabular-nums; }
  table.kalshi tr:last-child td { border-bottom: none; }
  table.kalshi tr:hover td { background: var(--accent-tint); cursor: pointer; }
  ul.edge-tips { list-style: none; margin: 12px 0 0; padding: 0; }
  ul.edge-tips li {
    padding: 8px 12px; margin-bottom: 6px; border-radius: 6px; font-size: 13.5px;
    background: var(--accent-tint);
  }
</style>
</head>
<body>
<header>
  <h1>WindBorne Monitor</h1>
  <p>Kalshi KXHIGH · WeatherMesh-6 + METAR en vivo · build v4.2</p>
</header>
<main>
  <div class="controls">
    <select id="ciudad"></select>
    <button class="tab active" data-tab="resumen">Resumen</button>
    <button class="tab" data-tab="grafico">Gráfico</button>
    <button class="tab" data-tab="kalshi">Kalshi (20)</button>
    <button class="tab" data-tab="historial">Historial</button>
    <button class="tab" data-tab="huracanes">Huracanes</button>
    <button id="refrescar" style="margin-left:auto">Refrescar</button>
  </div>

  <div id="tab-resumen"></div>
  <div id="tab-grafico" class="hidden"></div>
  <div id="tab-kalshi" class="hidden"></div>
  <div id="tab-historial" class="hidden"></div>
  <div id="tab-huracanes" class="hidden">
    <label><input type="checkbox" id="hur-global" /> Ver las 8 cuencas globales (no solo USA)</label>
    <div id="hur-body"></div>
  </div>
</main>

<script>
let ciudadActual = null;
let tabActual = 'resumen';

async function cargarCiudades() {
  const r = await fetch('/api/ciudades');
  const data = await r.json();
  const sel = document.getElementById('ciudad');
  sel.innerHTML = data.map(c => `<option value="${c.id}">${c.nombre} (${c.serie || 'USA'})</option>`).join('');
  ciudadActual = data[0].id;
  cargarTab();
}

function pillClass(p) {
  if (p === null || p === undefined) return 'mid';
  if (p >= 60) return 'hi';
  if (p >= 30) return 'mid';
  return 'lo';
}

async function cargarResumen() {
  const el = document.getElementById('tab-resumen');
  el.innerHTML = '<p class="muted">Cargando…</p>';
  const r = await fetch(`/api/resumen/${ciudadActual}`);
  const d = await r.json();
  if (d.error) {
    el.innerHTML = `<div class="error">${d.error}</div>`;
    return;
  }
  const probs = d.probs_pico || {};
  const pillsHtml = Object.keys(probs).map(u =>
    `<span class="pill ${pillClass(probs[u])}">≥${u}°F: ${probs[u]}%</span>`
  ).join('');
  const fuenteTxt = d.fuente === 'open-meteo'
    ? `<p class="muted">Fallback Open-Meteo (WindBorne: ${d.wb_error || 'no disponible'})</p>`
    : `<p class="muted">${d.init_txt || ''}</p>`;
  const metarHtml = d.metar
    ? `<p class="muted">METAR ${d.metar.station}: <b>${d.metar.temp_f}°F</b> (hace ${d.metar.age_min} min)</p>`
    : '';
  const mm = d.metar_max_hoy;
  const hw = d.pico_wm6_max_hoy;
  const pk = d.pico_kalshi;
  // Número grande: lo que importa para Kalshi (NWS max del día si hay; si no, max modelo)
  const picoDia = pk && pk.temp_f != null
    ? pk
    : (mm && mm.temp_f != null
        ? { temp_f: mm.temp_f, hora: mm.hora, fuente: 'NWS ' + (mm.station || '') }
        : { temp_f: d.pico_f, hora: d.pico_hora, fuente: 'WM-6 actual' });
  const heroHtml = `
    <div class="hero-pico">
      <div>
        <p class="label">Pico del día (Kalshi)</p>
        <p class="value">${picoDia.temp_f != null ? picoDia.temp_f + '°F' : '—'}</p>
        <p class="sub">${picoDia.fuente || ''}${picoDia.hora ? ' · @ ' + picoDia.hora : ''}</p>
      </div>
      <div class="muted" style="max-width:280px;font-size:12.5px;line-height:1.4">
        Máxima real del día (NWS) cuando hay observaciones.
        El modelo WM-6 solo pronostica y puede bajar después.
      </div>
    </div>`;
  let deltaHtml = '';
  if (mm && d.pico_f != null) {
    const delta = Math.round((d.pico_f - mm.temp_f) * 10) / 10;
    const signo = delta >= 0 ? '+' : '';
    deltaHtml = `<p class="muted" style="margin-top:10px">WM-6 actual vs max real NWS: <b>${signo}${delta}°F</b></p>`;
  }
  if (hw && d.pico_f != null && Number(hw.temp_f) !== Number(d.pico_f)) {
    deltaHtml += `<p class="muted">Modelo revisó a la baja: techo WM-6 hoy <b>${hw.temp_f}°F</b> → actual <b>${d.pico_f}°F</b></p>`;
  }
  el.innerHTML = `
    <div class="card">
      <h3 style="margin:0 0 4px">${d.city}</h3>
      ${fuenteTxt}
      ${metarHtml}
      ${heroHtml}
      <div class="grid">
        <div class="stat"><dt>Ahora</dt><dd>${d.ahora_f ?? '—'}°F</dd></div>
        <div class="stat"><dt>Pico WM-6 actual</dt><dd>${d.pico_f ?? '—'}°F</dd></div>
        <div class="stat"><dt>Techo WM-6 hoy</dt><dd>${hw && hw.temp_f != null ? hw.temp_f + '°F' : '—'}</dd></div>
        <div class="stat"><dt>Máx REAL NWS</dt><dd>${mm && mm.temp_f != null ? mm.temp_f + '°F' : '—'}</dd></div>
        <div class="stat"><dt>Mínimo</dt><dd>${d.min_f ?? '—'}°F</dd></div>
        <div class="stat"><dt>Promedio</dt><dd>${d.promedio ?? '—'}°F</dd></div>
      </div>
      ${deltaHtml}
      ${pillsHtml ? `<p style="margin-top:16px">${pillsHtml}</p>` : ''}
    </div>
    <div class="card" id="edge-card">
      <h3 style="margin:0 0 4px">Edge Kalshi</h3>
      <p class="muted" style="margin:0">Cargando…</p>
    </div>`;

  if (d.fuente === 'windborne') {
    const re = await fetch(`/api/edge/${ciudadActual}`);
    const de = await re.json();
    const cardEdge = document.getElementById('edge-card');
    if (de.error || !de.tips || de.tips.length === 0) {
      cardEdge.innerHTML = `<h3 style="margin:0 0 4px">Edge Kalshi</h3><p class="muted" style="margin:0">Sin edge claro por ahora.</p>`;
    } else {
      cardEdge.innerHTML = `
        <h3 style="margin:0 0 4px">Edge Kalshi</h3>
        <p class="muted" style="margin:0">Pico WM-6: <b>${de.pico_f}°F</b> @ ${de.pico_hora}</p>
        <ul class="edge-tips">${de.tips.map(t => `<li>${t}</li>`).join('')}</ul>
        <p class="muted" style="margin:8px 0 0">No es consejo financiero. Cruza con METAR y la corrida del modelo.</p>`;
    }
  } else {
    document.getElementById('edge-card').classList.add('hidden');
  }
}

async function cargarGrafico() {
  const el = document.getElementById('tab-grafico');
  el.innerHTML = `<div class="card"><img class="grafico" src="/api/grafico/${ciudadActual}.png?t=${Date.now()}" alt="Gráfico WM-6" /></div>`;
}

async function cargarKalshi() {
  const el = document.getElementById('tab-kalshi');
  el.innerHTML = '<p class="muted">Consultando 20 ciudades (1 llamada API)…</p>';
  const r = await fetch('/api/kalshi');
  const d = await r.json();
  const filas = (d.ciudades || []).map(c => {
    const maxHoy = (c.pico_max_hoy != null && c.pico_max_hoy !== c.pico_f)
      ? ` <span class="muted">(max ${c.pico_max_hoy})</span>` : '';
    return `
    <tr data-city="${c.id}">
      <td><b>${c.nombre}</b></td>
      <td>${c.pico_f}°F${maxHoy}</td>
      <td>${c.pico_hora}</td>
      <td><span class="pill ${pillClass(c.prob97)}">${c.prob97}%</span></td>
      <td>${c.metar_f != null ? c.metar_f + '°F' : '—'}</td>
    </tr>`;
  }).join('');
  const erroresHtml = (Array.isArray(d.errores) ? d.errores.length : d.errores && Object.keys(d.errores).length)
    ? `<p class="muted" style="margin-top:10px">Sin datos: ${Array.isArray(d.errores) ? d.errores.join(', ') : Object.keys(d.errores).join(', ')}</p>`
    : '';
  el.innerHTML = `
    <div class="card">
      <div class="table-wrap">
        <table class="kalshi">
          <thead><tr><th>Ciudad</th><th>Pico WM-6</th><th>Hora</th><th>P≥97°F</th><th>METAR real</th></tr></thead>
          <tbody>${filas}</tbody>
        </table>
      </div>
      ${erroresHtml}
    </div>`;
  el.querySelectorAll('tr[data-city]').forEach(row => {
    row.addEventListener('click', () => {
      ciudadActual = row.dataset.city;
      document.getElementById('ciudad').value = ciudadActual;
      document.querySelector('button.tab[data-tab="resumen"]').click();
    });
  });
}

async function cargarHistorial() {
  const el = document.getElementById('tab-historial');
  el.innerHTML = `<div class="card"><img class="grafico" src="/api/historial/${ciudadActual}.png?t=${Date.now()}"
    alt="Historial del pico" onerror="this.parentElement.innerHTML='<p class=muted>Todavía no hay suficiente historial guardado para ' + ciudadActual + '. Volvé a mirar más tarde (se guarda un punto cada vez que se consulta esta ciudad).</p>'" /></div>`;
}

async function cargarHuracanes() {
  const el = document.getElementById('hur-body');
  el.innerHTML = '<p class="muted">Cargando…</p>';
  const global_ = document.getElementById('hur-global').checked ? '1' : '0';
  const r = await fetch(`/api/huracanes?global=${global_}`);
  const d = await r.json();
  if (!d.activos || d.activos.length === 0) {
    el.innerHTML = '<p class="muted">Sin ciclones tropicales activos en las cuencas consultadas.</p>';
    return;
  }
  el.innerHTML = d.activos.map(s => `
    <div class="storm">
      <h4>${s.nombre} <span class="muted">(${s.id} · ${s.cuenca_nombre})</span></h4>
      <p class="muted" style="margin:0">
        ${s.lat != null ? `Posición: ${s.lat.toFixed(1)}, ${s.lon.toFixed(1)}` : 'Sin posición'}
        ${s.wind_kt != null ? ` · Viento: ${s.wind_kt} kt` : ''}
        ${s.mslp_hpa != null ? ` · ${s.mslp_hpa} hPa` : ''}
      </p>
    </div>
  `).join('');
}

function cargarTab() {
  if (tabActual === 'resumen') cargarResumen();
  else if (tabActual === 'grafico') cargarGrafico();
  else if (tabActual === 'kalshi') cargarKalshi();
  else if (tabActual === 'historial') cargarHistorial();
  else if (tabActual === 'huracanes') cargarHuracanes();
}

const TABS = ['resumen', 'grafico', 'kalshi', 'historial', 'huracanes'];

document.querySelectorAll('button.tab').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('button.tab').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    tabActual = btn.dataset.tab;
    TABS.forEach(t => document.getElementById(`tab-${t}`).classList.toggle('hidden', t !== tabActual));
    cargarTab();
  });
});

document.getElementById('ciudad').addEventListener('change', (e) => {
  ciudadActual = e.target.value;
  cargarTab();
});
document.getElementById('refrescar').addEventListener('click', cargarTab);
document.getElementById('hur-global').addEventListener('change', cargarHuracanes);

cargarCiudades();
</script>
</body>
</html>"""


@app.get("/")
def index():
    return Response(DASHBOARD_HTML, mimetype="text/html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
