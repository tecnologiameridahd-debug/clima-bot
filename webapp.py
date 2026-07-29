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


BUILD_VERSION = "v4.4-por-dia"


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


def _nws_extremos(city, fecha=None):
    """Max/min real NWS del día LOCAL de la ciudad (no mezcla días)."""
    mm = mn = None
    meta = {"fecha": None, "n_obs": 0, "sin_obs": False}
    try:
        from observations import metar_extremos_hoy
        from datetime import datetime as _dt

        if city.get("station"):
            fecha = fecha or _dt.now(city["tz"]).strftime("%Y-%m-%d")
            ext = metar_extremos_hoy(city["station"], city["tz"], fecha=fecha)
            if ext:
                st = ext.get("station") or city["station"]
                meta = {
                    "fecha": ext.get("fecha") or fecha,
                    "n_obs": ext.get("n_obs") or 0,
                    "sin_obs": bool(ext.get("sin_obs")),
                    "periodo": ext.get("periodo") or "dia_local",
                }
                if ext.get("max"):
                    mm = {**ext["max"], "station": st, "fecha": meta["fecha"]}
                if ext.get("min"):
                    mn = {**ext["min"], "station": st, "fecha": meta["fecha"]}
    except Exception as e:
        print(f"[webapp] nws: {e}")
    return mm, mn, meta


def _hw_modelo(city, pico_actual=None, hora_actual=None, fecha=None):
    """Techo modelo SOLO del día local pedido (city:YYYY-MM-DD)."""
    try:
        from analysis import pico_wm6_max_hoy
        from datetime import datetime as _dt

        fecha = fecha or _dt.now(city["tz"]).strftime("%Y-%m-%d")
        hw = pico_wm6_max_hoy(
            city["id"], fecha, pico_actual=pico_actual, hora_actual=hora_actual
        )
        if hw:
            hw = {**hw, "fecha": fecha}
        return hw
    except Exception as e:
        print(f"[webapp] hw: {e}")
        return None


def _pack_extremos(mm, mn):
    return {
        "metar_max_hoy": (
            {
                "temp_f": mm["temp_f"],
                "hora": mm.get("hora"),
                "station": mm.get("station"),
                "fecha": mm.get("fecha"),
            }
            if mm and mm.get("temp_f") is not None
            else None
        ),
        "metar_min_hoy": (
            {
                "temp_f": mn["temp_f"],
                "hora": mn.get("hora"),
                "station": mn.get("station"),
                "fecha": mn.get("fecha"),
            }
            if mn and mn.get("temp_f") is not None
            else None
        ),
    }


def _om_rapido(city):
    """Open-Meteo: ahora + max/min modelo del día (fallback si no hay WM-6)."""
    try:
        return resumen_om(city=city) or {}
    except Exception as e:
        print(f"[webapp] om: {e}")
        return {}


def _delta_txt(d, etiqueta="Modelo"):
    if d is None:
        return None
    if d > 0.3:
        return f"{etiqueta} {d:+.1f}°F más alto que el real"
    if d < -0.3:
        return f"{etiqueta} {d:+.1f}°F más bajo que el real"
    return f"{etiqueta} y real casi iguales ({d:+.1f}°F)"


@app.get("/api/resumen/<city_id>")
def api_resumen(city_id):
    """Siempre rellena: max/min REAL, max/min MODELO, ahora, discrepancia.

    REAL = NWS. MODELO = techo WM-6 registrado si hay; si no Open-Meteo.
    Denver seed de highwater no se toca (solo se lee).
    """
    try:
        city = _resolver_ciudad(city_id)
    except ValueError:
        return jsonify({"error": f"Ciudad desconocida: {city_id}"}), 404

    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
    from datetime import datetime as _dt

    # Día local de la ciudad (Denver ≠ NYC a la misma hora UTC)
    fecha_local = _dt.now(city["tz"]).strftime("%Y-%m-%d")

    # Paralelo: NWS (real de ESE día) + Open-Meteo (modelo/ahora del día)
    hw = _hw_modelo(city, fecha=fecha_local)
    mm = mn = None
    nws_meta = {"fecha": fecha_local, "n_obs": 0, "sin_obs": True}
    om = {}
    pool = ThreadPoolExecutor(max_workers=2)
    try:
        f_nws = pool.submit(_nws_extremos, city, fecha_local)
        f_om = pool.submit(_om_rapido, city)
        try:
            mm, mn, nws_meta = f_nws.result(timeout=12)
        except FutTimeout:
            print("[webapp] NWS timeout")
        except Exception as e:
            print(f"[webapp] NWS: {e}")
        try:
            om = f_om.result(timeout=10) or {}
        except FutTimeout:
            print("[webapp] OM timeout")
        except Exception as e:
            print(f"[webapp] OM: {e}")
    finally:
        pool.shutdown(wait=False)

    # --- REAL (NWS) ---
    max_real = mm["temp_f"] if mm and mm.get("temp_f") is not None else None
    min_real = mn["temp_f"] if mn and mn.get("temp_f") is not None else None

    # --- MODELO: techo WM-6 si existe; si no (o es menor), rellenar con Open-Meteo ---
    om_max = om.get("pico_hoy")
    om_min = om.get("min_hoy")
    if om_max is not None:
        try:
            om_max = round(float(om_max), 1)
        except (TypeError, ValueError):
            om_max = None
    if om_min is not None:
        try:
            om_min = round(float(om_min), 1)
        except (TypeError, ValueError):
            om_min = None

    # Highwater WM-6 no baja con OM. OM solo rellena si no hay techo, o si es mayor.
    max_modelo = None
    max_modelo_fuente = None
    max_modelo_hora = None
    if hw and hw.get("temp_f") is not None:
        max_modelo = float(hw["temp_f"])
        max_modelo_fuente = "WM-6 techo registrado"
        max_modelo_hora = hw.get("hora")
    if om_max is not None:
        if max_modelo is None:
            max_modelo = om_max
            max_modelo_fuente = "Open-Meteo"
            max_modelo_hora = None
        elif om_max > max_modelo:
            max_modelo = om_max
            max_modelo_fuente = "Open-Meteo (mayor que techo WM-6)"
            max_modelo_hora = None
            try:
                from analysis import pico_wm6_max_hoy
                from datetime import datetime as _dt

                pico_wm6_max_hoy(
                    city["id"],
                    fecha_local,
                    pico_actual=om_max,
                    hora_actual="OM",
                )
            except Exception:
                pass
    if max_modelo is not None:
        max_modelo = round(float(max_modelo), 1)

    min_modelo = om_min
    min_modelo_fuente = "Open-Meteo" if om_min is not None else None

    ahora_f = om.get("ahora_f")
    ahora_hora = om.get("hora_ahora")

    pico_kalshi = None
    if max_real is not None:
        pico_kalshi = {
            "temp_f": max_real,
            "hora": (mm or {}).get("hora"),
            "fuente": f"NWS {(mm or {}).get('station') or city.get('station') or ''}".strip(),
        }

    # Discrepancias
    disc = {"filas": []}
    if max_real is not None and max_modelo is not None:
        dmax = round(float(max_modelo) - float(max_real), 1)
        disc["max_modelo_menos_real"] = dmax
        disc["max_lectura"] = _delta_txt(dmax, "Máx modelo")
        disc["filas"].append(
            {
                "label": "Máx MODELO − Máx REAL",
                "delta": dmax,
                "a": max_modelo,
                "b": max_real,
            }
        )
    if min_real is not None and min_modelo is not None:
        dmin = round(float(min_modelo) - float(min_real), 1)
        disc["min_modelo_menos_real"] = dmin
        disc["min_lectura"] = _delta_txt(dmin, "Mín modelo")
        disc["filas"].append(
            {
                "label": "Mín MODELO − Mín REAL",
                "delta": dmin,
                "a": min_modelo,
                "b": min_real,
            }
        )
    disc["nota"] = (
        "REAL = NWS (Kalshi). MODELO = techo WM-6 si hay, si no Open-Meteo. "
        "Los valores no se reescriben entre sí; solo se comparan."
    )

    return jsonify(
        {
            "city": city["nombre"],
            "city_id": city.get("id"),
            "fecha_local": fecha_local,
            "tz": str(city.get("tz") or ""),
            "fuente": "nws+modelo",
            "nws_n_obs": nws_meta.get("n_obs"),
            "nws_sin_obs": nws_meta.get("sin_obs"),
            "ahora_f": ahora_f,
            "ahora_hora": ahora_hora,
            "pico_f": max_modelo,
            "pico_hora": max_modelo_hora,
            "min_f": min_modelo,
            "min_hora": None,
            "min_modelo": (
                {
                    "temp_f": min_modelo,
                    "fuente": min_modelo_fuente,
                    "fecha": fecha_local,
                }
                if min_modelo is not None
                else None
            ),
            "max_modelo": (
                {
                    "temp_f": max_modelo,
                    "hora": max_modelo_hora,
                    "fuente": max_modelo_fuente,
                    "fecha": fecha_local,
                }
                if max_modelo is not None
                else None
            ),
            "pico_wm6_max_hoy": (
                {
                    "temp_f": max_modelo,
                    "hora": max_modelo_hora,
                    "fuente": max_modelo_fuente,
                    "fecha": fecha_local,
                }
                if max_modelo is not None
                else None
            ),
            "pico_kalshi": pico_kalshi,
            "discrepancia": disc,
            **_pack_extremos(mm, mn),
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
  .hero-row {
    display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin: 12px 0 16px;
  }
  @media (max-width: 640px) { .hero-row { grid-template-columns: 1fr; } }
  .hero-pico {
    display: flex; flex-direction: column; gap: 4px; padding: 16px 18px; border-radius: 10px;
    background: linear-gradient(135deg, var(--accent-tint), transparent);
    border: 1px solid var(--accent);
  }
  .hero-pico.min {
    background: linear-gradient(135deg, var(--ok-tint), transparent);
    border-color: var(--ok);
  }
  .hero-pico.model {
    background: linear-gradient(135deg, var(--warn-tint), transparent);
    border-color: var(--warn);
  }
  .hero-pico .label { font-size: 12px; text-transform: uppercase; letter-spacing: .06em; color: var(--ink-soft); margin: 0; }
  .hero-pico .value { font-size: 36px; font-weight: 700; font-variant-numeric: tabular-nums; color: var(--accent); line-height: 1.1; margin: 0; }
  .hero-pico.min .value { color: var(--ok); }
  .hero-pico.model .value { color: var(--warn); }
  .hero-pico .sub { margin: 0; font-size: 13px; color: var(--ink-soft); }
  .disc-box {
    margin-top: 14px; padding: 14px 16px; border-radius: 8px;
    border: 1px solid var(--line); background: var(--code-bg);
  }
  .disc-box h4 { margin: 0 0 8px; font-size: 13px; text-transform: uppercase; letter-spacing: .04em; color: var(--ink-soft); }
  .disc-box .row { display: flex; flex-wrap: wrap; gap: 8px 18px; margin: 6px 0; font-size: 14px; font-variant-numeric: tabular-nums; }
  .disc-box .tag { font-weight: 600; }
  .disc-box .tag.pos { color: var(--danger); }
  .disc-box .tag.neg { color: var(--ok); }
  .disc-box .tag.neu { color: var(--ink-soft); }
  .disc-box .hint { margin: 10px 0 0; font-size: 12.5px; color: var(--ink-soft); line-height: 1.4; }
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
  <p>Kalshi KXHIGH · max/min por día local · build v4.4</p>
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
  const fechaTxt = d.fecha_local
    ? `<p class="muted"><b>Día local:</b> ${d.fecha_local}${d.tz ? ' · ' + d.tz : ''}${d.nws_sin_obs ? ' · <i>aún pocas obs NWS de este día</i>' : (d.nws_n_obs != null ? ' · ' + d.nws_n_obs + ' obs NWS' : '')}</p>`
    : '';
  const fuenteTxt = d.fuente === 'open-meteo'
    ? `<p class="muted">Fallback Open-Meteo (WindBorne: ${d.wb_error || 'no disponible'})</p>`
    : `<p class="muted">${d.init_txt || ''}</p>`;
  const metarHtml = d.metar
    ? `<p class="muted">METAR ${d.metar.station}: <b>${d.metar.temp_f}°F</b> (hace ${d.metar.age_min} min)</p>`
    : '';
  const mm = d.metar_max_hoy;
  const mn = d.metar_min_hoy;
  const mx = d.max_modelo || d.pico_wm6_max_hoy;
  const mnMod = d.min_modelo;
  const maxReal = (mm && mm.temp_f != null)
    ? { temp_f: mm.temp_f, hora: mm.hora, fuente: 'REAL NWS ' + (mm.station || '') }
    : null;
  const maxModelo = (mx && mx.temp_f != null)
    ? { temp_f: mx.temp_f, hora: mx.hora, fuente: mx.fuente || 'Modelo' }
    : (d.pico_f != null ? { temp_f: d.pico_f, hora: d.pico_hora, fuente: 'Modelo' } : null);
  const minReal = (mn && mn.temp_f != null)
    ? { temp_f: mn.temp_f, hora: mn.hora, fuente: 'REAL NWS ' + (mn.station || '') }
    : null;
  const minModelo = (mnMod && mnMod.temp_f != null)
    ? { temp_f: mnMod.temp_f, fuente: mnMod.fuente || 'Modelo' }
    : (d.min_f != null ? { temp_f: d.min_f, fuente: 'Modelo' } : null);
  const heroHtml = `
    <div class="hero-row">
      <div class="hero-pico">
        <p class="label">Máximo REAL del día</p>
        <p class="value">${maxReal ? maxReal.temp_f + '°F' : '—'}</p>
        <p class="sub">${maxReal ? ((maxReal.fuente || '') + (maxReal.hora ? ' · @ ' + maxReal.hora : '')) : 'NWS sin datos'}</p>
      </div>
      <div class="hero-pico model">
        <p class="label">Máximo MODELO</p>
        <p class="value">${maxModelo ? maxModelo.temp_f + '°F' : '—'}</p>
        <p class="sub">${maxModelo ? ((maxModelo.fuente || '') + (maxModelo.hora ? ' · @ ' + maxModelo.hora : '')) : 'sin modelo'}</p>
      </div>
      <div class="hero-pico min">
        <p class="label">Mínimo REAL del día</p>
        <p class="value">${minReal ? minReal.temp_f + '°F' : '—'}</p>
        <p class="sub">${minReal ? ((minReal.fuente || '') + (minReal.hora ? ' · @ ' + minReal.hora : '')) : 'NWS sin datos'}</p>
      </div>
      <div class="hero-pico model">
        <p class="label">Mínimo MODELO</p>
        <p class="value">${minModelo ? minModelo.temp_f + '°F' : '—'}</p>
        <p class="sub">${minModelo ? (minModelo.fuente || 'Modelo') : 'sin modelo'}</p>
      </div>
    </div>`;
  // Discrepancia
  const disc = d.discrepancia || {};
  let discRows = '';
  const filas = disc.filas || [];
  if (filas.length) {
    filas.forEach(f => {
      const cls = f.delta > 0.3 ? 'pos' : (f.delta < -0.3 ? 'neg' : 'neu');
      const signo = f.delta > 0 ? '+' : '';
      discRows += `<div class="row">${f.label}: <span class="tag ${cls}">${signo}${f.delta}°F</span>
        <span class="muted">(${f.a} − ${f.b})</span></div>`;
    });
  } else {
    if (maxReal && maxModelo) {
      const dmax = Math.round((Number(maxModelo.temp_f) - Number(maxReal.temp_f)) * 10) / 10;
      const cls = dmax > 0.3 ? 'pos' : (dmax < -0.3 ? 'neg' : 'neu');
      const signo = dmax > 0 ? '+' : '';
      discRows += `<div class="row">Máx MODELO − Máx REAL: <span class="tag ${cls}">${signo}${dmax}°F</span></div>`;
    }
    if (minReal && minModelo) {
      const dmin = Math.round((Number(minModelo.temp_f) - Number(minReal.temp_f)) * 10) / 10;
      const cls = dmin > 0.3 ? 'pos' : (dmin < -0.3 ? 'neg' : 'neu');
      const signo = dmin > 0 ? '+' : '';
      discRows += `<div class="row">Mín MODELO − Mín REAL: <span class="tag ${cls}">${signo}${dmin}°F</span></div>`;
    }
  }
  if (disc.max_lectura) discRows += `<div class="row muted">${disc.max_lectura}</div>`;
  if (disc.min_lectura) discRows += `<div class="row muted">${disc.min_lectura}</div>`;
  if (!discRows) discRows = `<div class="row muted">Faltan datos para comparar (espera NWS o modelo).</div>`;
  const discHtml = `
    <div class="disc-box">
      <h4>Discrepancia entre fuentes</h4>
      ${discRows}
      <p class="hint">${disc.nota || 'REAL = NWS (Kalshi). MODELO = WM-6 techo o Open-Meteo.'}</p>
    </div>`;
  const ahoraTxt = d.ahora_f != null
    ? `${d.ahora_f}°F${d.ahora_hora ? ' @ ' + d.ahora_hora : ''}`
    : '—';
  el.innerHTML = `
    <div class="card">
      <h3 style="margin:0 0 4px">${d.city}</h3>
      ${fechaTxt}
      ${fuenteTxt}
      ${metarHtml}
      ${heroHtml}
      <div class="grid" style="margin-top:12px">
        <div class="stat"><dt>Ahora (modelo/OM)</dt><dd>${ahoraTxt}</dd></div>
        <div class="stat"><dt>Máx REAL</dt><dd>${maxReal ? maxReal.temp_f + '°F' : '—'}</dd></div>
        <div class="stat"><dt>Máx MODELO</dt><dd>${maxModelo ? maxModelo.temp_f + '°F' : '—'}</dd></div>
        <div class="stat"><dt>Mín REAL</dt><dd>${minReal ? minReal.temp_f + '°F' : '—'}</dd></div>
        <div class="stat"><dt>Mín MODELO</dt><dd>${minModelo ? minModelo.temp_f + '°F' : '—'}</dd></div>
      </div>
      ${discHtml}
    </div>
    <div class="card hidden" id="edge-card"></div>`;
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
