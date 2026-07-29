import csv
import math
import os
from datetime import datetime

from api import (
    batch_cache_age,
    city_raw_from_batch,
    fetch_all_cities_forecast,
    fetch_forecast,
    get_points_hoy,
)
from cities import DEFAULT_CITY_ID, KALSHI_CITIES, get_city
from config import UMBRALES_F, UMBRAL_ALERTA_CAMBIO, log_csv_path
from observations import get_metar_obs, metar_linea, metar_max_hoy
from utils import c_to_f
from wb_interp import enriquecer_min, enriquecer_pico, futuros_sin_repetir, texto_hora


def dist_f(dist):
    if not dist:
        return {}
    return {k: c_to_f(dist[k]) for k in ("mean", "p10", "p25", "p75", "p90") if dist.get(k) is not None}


def prob_umbral(dist, umbral_f):
    if not dist:
        return None
    mean_c = dist.get("mean")
    std_c = dist.get("std") or 0
    if mean_c is None:
        return None
    threshold_c = (umbral_f - 32) * 5 / 9
    if std_c <= 0:
        return 100 if c_to_f(mean_c) >= umbral_f else 0
    z = (threshold_c - mean_c) / std_c
    prob_below = 0.5 * (1 + math.erf(z / math.sqrt(2)))
    return round((1 - prob_below) * 100)


def analizar_desde_raw(raw, city):
    puntos = get_points_hoy(raw, city=city)
    if not puntos:
        return None

    ahora = datetime.now(city["tz"])
    punto_ahora = min(puntos, key=lambda p: abs((p["dt"] - ahora).total_seconds()))
    punto_pico = max(puntos, key=lambda p: p["temp_f"])
    punto_pico = enriquecer_pico(punto_pico, puntos)
    punto_min = enriquecer_min(puntos)
    futuros = futuros_sin_repetir(puntos, punto_ahora, punto_pico)
    temps = [p["temp_f"] for p in puntos]

    init = raw.get("initialization_time", "")
    init_txt = "N/D"
    modelo_hace_min = None
    if init:
        init_dt = datetime.fromisoformat(init.replace("Z", "+00:00")).astimezone(city["tz"])
        modelo_hace_min = int((ahora - init_dt).total_seconds() / 60)
        init_txt = f"Corrida WM-6: {init_dt.strftime('%H:%M')} (hace {modelo_hace_min} min)"

    metar = get_metar_obs(city.get("station"))
    consulta_seg = batch_cache_age()

    probs_horas = {
        u: round(sum(1 for t in temps if t >= u) / len(temps) * 100) for u in UMBRALES_F
    }
    probs_pico = {u: prob_umbral(punto_pico.get("dist"), u) for u in UMBRALES_F}

    return {
        "city": city,
        "fecha": ahora.strftime("%Y-%m-%d"),
        "init_txt": init_txt,
        "modelo_hace_min": modelo_hace_min,
        "consulta_seg": consulta_seg,
        "metar": metar,
        "metar_max_hoy": None,
        "ahora": punto_ahora,
        "pico": punto_pico,
        "minimo": punto_min,
        "futuros": futuros,
        "max_dia": punto_pico["temp_f"],
        "min_dia": punto_min["temp_f"] if punto_min.get("temp_f") is not None else min(temps),
        "promedio": round(sum(temps) / len(temps), 1),
        "slots_3h": len(puntos),
        "probs_horas": probs_horas,
        "probs_pico": probs_pico,
        "puntos": puntos,
        "raw": raw,
    }


def _leer_filas_log(city_id):
    path = log_csv_path(city_id)
    if not os.path.exists(path):
        return []
    try:
        with open(path, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except Exception:
        try:
            with open(path, newline="") as f:
                return list(csv.DictReader(f))
        except Exception:
            return []


def pico_wm6_max_hoy(city_id, fecha, pico_actual=None, hora_actual=None):
    """Máximo pico WM-6 visto hoy (high-water mark).

    El modelo se re-corre y a veces baja el pico de la tarde. Para Kalshi
    conviene recordar el techo que el modelo llegó a dar en el día, no solo
    la última corrida.
    """
    mejor_f = None
    mejor_hora = None
    for row in _leer_filas_log(city_id):
        ts = row.get("time") or ""
        if not ts.startswith(fecha):
            continue
        try:
            pf = float(row["pico_f"])
        except (KeyError, TypeError, ValueError):
            continue
        if mejor_f is None or pf > mejor_f:
            mejor_f = pf
            mejor_hora = row.get("pico_hora") or mejor_hora
    if pico_actual is not None:
        try:
            pa = float(pico_actual)
        except (TypeError, ValueError):
            pa = None
        if pa is not None and (mejor_f is None or pa > mejor_f):
            mejor_f = pa
            mejor_hora = hora_actual or mejor_hora
    if mejor_f is None:
        return None
    return {"temp_f": round(mejor_f, 1), "hora": mejor_hora or "?"}


def _adjuntar_pico_dia(a):
    """Añade campos de pico del día (high-water + máx real) al análisis."""
    if not a:
        return a
    city = a["city"]
    hw = pico_wm6_max_hoy(
        city["id"],
        a["fecha"],
        pico_actual=a["pico"]["temp_f"],
        hora_actual=a["pico"].get("hora"),
    )
    a["pico_wm6"] = {
        "temp_f": a["pico"]["temp_f"],
        "hora": a["pico"].get("hora"),
    }
    a["pico_wm6_max_hoy"] = hw
    # Referencia Kalshi: el techo real (NWS) manda; si aún no hay obs, el max modelo del día
    mm = a.get("metar_max_hoy")
    if mm and mm.get("temp_f") is not None:
        a["pico_kalshi"] = {
            "temp_f": mm["temp_f"],
            "hora": mm.get("hora"),
            "fuente": f"NWS {mm.get('station') or city.get('station') or ''}".strip(),
        }
    elif hw:
        a["pico_kalshi"] = {
            "temp_f": hw["temp_f"],
            "hora": hw.get("hora"),
            "fuente": "WM-6 max hoy (sin máx real aún)",
        }
    else:
        a["pico_kalshi"] = {
            "temp_f": a["pico"]["temp_f"],
            "hora": a["pico"].get("hora"),
            "fuente": "WM-6 actual",
        }
    return a


def analizar(city=None, force=False):
    city = city or get_city(DEFAULT_CITY_ID)
    raw = fetch_forecast(city=city, force=force)
    a = analizar_desde_raw(raw, city)
    # Solo para 1 ciudad: en analizar_todas (20 a la vez) se omite para no
    # sumar 20 llamadas a NWS y volver lenta la vista multi-ciudad.
    if a and city.get("station"):
        a["metar_max_hoy"] = metar_max_hoy(city["station"], city["tz"])
    return _adjuntar_pico_dia(a)


def analizar_todas(force=False):
    batch = fetch_all_cities_forecast(force=force)
    resultados = []
    errores = []
    for cid in KALSHI_CITIES:
        city = get_city(cid)
        try:
            raw = city_raw_from_batch(cid, batch)
            a = analizar_desde_raw(raw, city)
            if a:
                resultados.append(_adjuntar_pico_dia(a))
            else:
                errores.append(city["nombre"])
        except Exception as e:
            errores.append(f"{city['nombre']} ({e})")
    return resultados, errores


def log_peak(a):
    city = a["city"]
    path = log_csv_path(city["id"])
    # Siempre se guarda el pico de la corrida actual (no el high-water),
    # para poder reconstruir el techo del día y ver revisiones del modelo.
    fila = {
        "time": datetime.now(city["tz"]).isoformat(),
        "pico_f": a["pico"]["temp_f"],
        "pico_hora": a["pico"]["hora"],
        "ahora_f": a["ahora"]["temp_f"],
        "prob_97": a["probs_pico"].get(97),
        "prob_98": a["probs_pico"].get(98),
    }
    existe = os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fila.keys())
        if not existe:
            w.writeheader()
        w.writerow(fila)
    # Refrescar high-water con la fila recién escrita
    _adjuntar_pico_dia(a)
    return fila


def detectar_cambio_pico(nuevo_pico, city_id):
    rows = _leer_filas_log(city_id)
    if not rows:
        return None
    try:
        anterior = float(rows[-1]["pico_f"])
    except (KeyError, TypeError, ValueError):
        return None
    diff = round(nuevo_pico - anterior, 1)
    if abs(diff) >= UMBRAL_ALERTA_CAMBIO:
        return {"anterior": anterior, "nuevo": nuevo_pico, "diff": diff}
    return None


def msg_kalshi_todas(force=False):
    if force:
        from observations import fetch_all_metar

        fetch_all_metar(force=True)

    edad = batch_cache_age()
    resultados, errores = analizar_todas(force=force)
    lineas = ["<b>Kalshi KXHIGH — WindBorne + METAR</b>"]
    if edad is not None and not force:
        lineas.append(f"<i>WM-6 consultado hace {edad}s · METAR en vivo</i>\n")
    else:
        lineas.append("<i>WM-6 + METAR actualizados</i>\n")

    for a in resultados:
        city = a["city"]
        p = a["pico"]
        hw = a.get("pico_wm6_max_hoy") or {}
        obs_txt = ""
        m = a.get("metar")
        if m and m.get("temp_f") is not None:
            edad_obs = f"{m['age_min']}m" if m.get("age_min") is not None else "?"
            obs_txt = f" | <b>Real: {m['temp_f']}°F</b> ({edad_obs})"
        max_txt = ""
        if hw.get("temp_f") is not None and hw["temp_f"] != p["temp_f"]:
            max_txt = f" | max hoy {hw['temp_f']}°F"
        lineas.append(
            f"<b>{city['nombre']}</b>: "
            f"Pico {p['temp_f']}°F @ {p['hora']}{max_txt}{obs_txt}  "
            f"P≥97: {a['probs_pico'].get(97, 0)}%"
        )

    if errores:
        lineas.append("\n<i>Sin datos: " + ", ".join(errores[:5]) + "</i>")
    return "\n".join(lineas)


# --- Formatos Telegram ---

def msg_ahora(a):
    p = a["ahora"]
    d = dist_f(p.get("dist"))
    c = a["city"]
    metar_txt = (
        metar_linea(c["station"])
        if c.get("station")
        else "Observación METAR: sin estación cercana"
    )
    lineas = [
        f"<b>{c['nombre']} — AHORA</b>",
        metar_txt,
        f"Pronóstico WB ({p['hora']}): <b>{p['temp_f']}°F</b>",
        f"Rango IA: {d.get('p25', p['temp_f'])}–{d.get('p75', p['temp_f'])}°F",
        a["init_txt"],
    ]
    if a.get("consulta_seg") is not None:
        lineas.append(f"Consulta API: hace {a['consulta_seg']}s")
    return "\n".join(lineas)


def msg_pico(a):
    p = a["pico"]
    d = dist_f(p.get("dist"))
    probs = a["probs_pico"]
    c = a["city"]["nombre"]
    lineas = "\n".join(f"≥ {u}°F → <b>{probs.get(u, 0)}%</b>" for u in [95, 96, 97, 98])
    hw = a.get("pico_wm6_max_hoy") or {}
    mm = a.get("metar_max_hoy") or {}
    pk = a.get("pico_kalshi") or {}
    extra = []
    if hw.get("temp_f") is not None and hw["temp_f"] != p["temp_f"]:
        extra.append(
            f"Pico WM-6 más alto hoy: <b>{hw['temp_f']}°F</b> "
            f"(corridas previas; la actual bajó a {p['temp_f']}°F)"
        )
    if mm.get("temp_f") is not None:
        extra.append(
            f"Máx REAL NWS ({mm.get('station', '?')}): <b>{mm['temp_f']}°F</b> @ {mm.get('hora', '?')}"
        )
    if pk.get("temp_f") is not None:
        extra.append(
            f"Referencia Kalshi: <b>{pk['temp_f']}°F</b> ({pk.get('fuente', '')})"
        )
    extra_txt = ("\n" + "\n".join(extra) + "\n") if extra else ""
    return (
        f"<b>{c} — PICO ({texto_hora(p)})</b>\n"
        f"Pico WM-6 actual: <b>{p['temp_f']}°F</b>\n"
        f"<i>(última corrida, {a['slots_3h']} puntos cada 3h)</i>\n"
        f"{extra_txt}"
        f"Rango p25–p75: {d.get('p25', '?')}–{d.get('p75', '?')}°F\n"
        f"Rango p10–p90: {d.get('p10', '?')}–{d.get('p90', '?')}°F\n\n"
        f"<b>Probabilidades IA en el pico actual:</b>\n{lineas}"
    )


def msg_horas(a):
    lineas = []
    for p in a["futuros"][:8]:
        d = dist_f(p.get("dist"))
        p25, p75 = d.get("p25"), d.get("p75")
        if p25 is not None and p75 is not None:
            lineas.append(f"🕒 {p['hora']} — med {p['temp_f']}°F · IA {p25}–{p75}°F")
        else:
            lineas.append(f"🕒 {p['hora']} — {p['temp_f']}°F")
    if not lineas:
        lineas.append("Sin más horas futuras (ahora y pico arriba).")
    c = a["city"]["nombre"]
    return f"<b>{c} — próximas horas</b>\n" + "\n".join(lineas)


def msg_prob(a):
    ph = a["probs_horas"]
    pp = a["probs_pico"]
    c = a["city"]
    lineas_h = "\n".join(f"≥ {u}°F → {ph.get(u, 0)}%" for u in UMBRALES_F)
    lineas_p = "\n".join(f"≥ {u}°F → <b>{pp.get(u, 0)}%</b>" for u in UMBRALES_F)
    return (
        f"<b>Probabilidades Kalshi — {c['nombre']} ({c['serie']})</b>\n"
        f"{a['fecha']} · estación {c['station']}\n\n"
        f"<b>En el PICO (distribución IA):</b>\n{lineas_p}\n\n"
        f"<b>Por horas restantes:</b>\n{lineas_h}"
    )


def msg_resumen(a):
    c = a["city"]
    serie = c.get("serie") or "USA"
    station = c.get("station") or "sin METAR"
    extra = ""
    if c.get("custom") and c.get("nombre_largo"):
        extra = f" ({c['nombre_largo']})"
    metar_txt = (
        metar_linea(c["station"])
        if c.get("station")
        else "Observación METAR: sin estación cercana"
    )
    lineas = [
        f"<b>WindBorne — {c['nombre']}</b>{extra}",
        f"{serie} · {station}",
        metar_txt,
        a["init_txt"],
    ]
    if a.get("consulta_seg") is not None:
        lineas.append(f"Consulta API: hace {a['consulta_seg']}s")
    m = a.get("metar")
    if m and m.get("temp_f") is not None:
        delta = round(m["temp_f"] - a["ahora"]["temp_f"], 1)
        sign = "+" if delta >= 0 else ""
        lineas.append(f"Δ Real−WB ahora: <b>{sign}{delta}°F</b>")
    lineas += [
        "",
        f"Pronóstico ahora: <b>{a['ahora']['temp_f']}°F</b> ({a['ahora']['hora']})",
        f"Pico WM-6 actual: <b>{a['pico']['temp_f']}°F</b> ({texto_hora(a['pico'])})",
    ]
    hw = a.get("pico_wm6_max_hoy")
    if hw and hw.get("temp_f") is not None and hw["temp_f"] != a["pico"]["temp_f"]:
        lineas.append(
            f"Pico WM-6 más alto hoy: <b>{hw['temp_f']}°F</b> "
            f"(no baja si el modelo revisa a la baja)"
        )
    mm = a.get("metar_max_hoy")
    if mm and mm.get("temp_f") is not None:
        lineas.append(
            f"Máx REAL hoy (NWS {mm['station']}): <b>{mm['temp_f']}°F</b> @ {mm['hora']}"
        )
    pk = a.get("pico_kalshi")
    if pk and pk.get("temp_f") is not None:
        lineas.append(
            f"Pico del día (Kalshi): <b>{pk['temp_f']}°F</b> — {pk.get('fuente', '')}"
        )
    lineas += [
        f"Mínimo del día: <b>{a['min_dia']}°F</b> ({texto_hora(a.get('minimo') or {})})",
        f"Promedio slots: {a['promedio']}°F · {a['slots_3h']} puntos/3h",
        "",
        f"P≥97°: <b>{a['probs_pico'].get(97, 0)}%</b>  "
        f"P≥98°: <b>{a['probs_pico'].get(98, 0)}%</b>",
    ]
    tips = edge_kalshi(a)
    if tips:
        lineas.append("")
        lineas.append("<b>Edge rápido:</b>")
        for tip in tips[:2]:
            lineas.append(f"• {tip}")
    return "\n".join(lineas)


def umbrales_para_ciudad(city, pico_f=None):
    base = list(UMBRALES_F)
    if pico_f is None:
        return base
    centro = int(round(float(pico_f)))
    dyn = [centro + d for d in range(-3, 4)]
    merged = sorted(set(base + dyn))
    merged.sort(key=lambda u: abs(u - float(pico_f)))
    return sorted(merged[:8])


def edge_kalshi(a):
    pico = a["pico"]["temp_f"]
    probs = dict(a.get("probs_pico") or {})
    c = a["city"]
    tips = []
    for u in umbrales_para_ciudad(c, pico):
        p = probs.get(u)
        if p is None:
            p = prob_umbral(a["pico"].get("dist"), u)
            if p is not None:
                probs[u] = p
        if p is None:
            continue
        if p >= 75 and u <= pico + 1:
            tips.append(f"≥{u}°F → {p}% (sesgo YES fuerte)")
        elif p <= 25 and u >= pico - 1:
            tips.append(f"≥{u}°F → {p}% (sesgo NO fuerte)")
    if not tips:
        cercanas = sorted(
            (
                (abs((probs.get(u) or 50) - 50), u, probs.get(u))
                for u in umbrales_para_ciudad(c, pico)
            ),
        )[:3]
        for _, u, p in cercanas:
            if p is not None:
                tips.append(f"≥{u}°F → {p}% (cerca del 50 · más incierto)")
    return tips[:4]


def msg_edge(a):
    tips = edge_kalshi(a)
    c = a["city"]
    cuerpo = "\n".join(f"• {t}" for t in tips) if tips else "Sin edge claro."
    return (
        f"<b>Edge Kalshi (educativo) — {c['nombre']}</b>\n"
        f"Pico WM-6: <b>{a['pico']['temp_f']}°F</b> @ {texto_hora(a['pico'])}\n"
        f"Serie: {c.get('serie') or 'N/D'}\n\n"
        f"{cuerpo}\n\n"
        f"<i>No es consejo financiero. Cruza con METAR y corrida del modelo.</i>"
    )


def msg_status_wb():
    from api import api_status
    from config import BATCH_CACHE_TTL, PREFETCH_INTERVAL

    s = api_status()
    age = s.get("batch_age")
    age_txt = f"{age}s" if age is not None else "sin batch"
    cuota = "⛔ AGOTADA (2000 free trial)" if s["quota_exhausted"] else "OK / desconocida"
    return (
        f"<b>WindBorne — estado API</b>\n"
        f"Key: …{s['key_tail']} ({'formato OK' if s['key_ok'] else 'rara'})\n"
        f"Cuota free trial: <b>{cuota}</b>\n"
        f"OK: {s['ok']} · fail: {s['fail']} · quota hits: {s['quota_hits']}\n"
        f"Cache hit: {s['cache_hit']} · disk: {s['disk_hit']}\n"
        f"Batch age: {age_txt} (TTL {BATCH_CACHE_TTL}s)\n"
        f"Prefetch cada {PREFETCH_INTERVAL}s\n"
        f"Último OK: {str(s['last_ok_ago']) + 's atrás' if s['last_ok_ago'] is not None else 'nunca'}\n"
        f"Último error: {s['last_error'] or '—'}\n\n"
        f"<i>Si cuota agotada: /resumen usa Open-Meteo; /manana sigue OK. "
        f"Sube plan WindBorne o usa key con cuota.</i>"
    )
