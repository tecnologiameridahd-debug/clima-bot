from datetime import datetime

from api import (
    WindBorneError,
    WindBorneRateLimitError,
    fetch_variable,
    fetch_variables,
    parse_points,
)
from cities import DEFAULT_CITY_ID, get_city
from utils import es_error_cuota
from wb_interp import texto_hora

TORMENTA_VARS = [
    "cape",
    "p_type",
    "low_cloud_cover",
    "cloud_base_height",
    "boundary_layer_height",
]

REPORTE_VARS = [
    "max_temperature_2m_3h",
    "min_temperature_2m_3h",
    "total_precipitation_3h",
    "cape",
    "p_type",
    "low_cloud_cover",
    "cloud_base_height",
    "boundary_layer_height",
    "skin_temperature",
    "dewpoint_2m",
    "wind_speed_10m",
]


def _fmt_err(exc):
    if isinstance(exc, WindBorneError):
        return str(exc)
    msg = str(exc)
    if es_error_cuota(msg):
        return (
            "⛔ Cuota WindBorne agotada (free trial 2000/2000). "
            "Hay que subir de plan o usar otra key."
        )
    if "429" in msg:
        return "Límite temporal WindBorne (429). Espera 1–2 min."
    if "401" in msg or "403" in msg:
        return "Key WindBorne inválida. Revisa WINDBORNE_API_KEY."
    return msg[:100]

# Variables extra con distribución IA (WM-6)
VARIABLES_EXTRA = {
    "max_temperature_2m_3h": {
        "nombre": "Máxima 3h",
        "unidad": "°F",
        "modo": "max",
        "convert": lambda v: round(v * 9 / 5 + 32, 1),
        "kalshi": True,
    },
    "min_temperature_2m_3h": {
        "nombre": "Mínima 3h",
        "unidad": "°F",
        "modo": "min",
        "convert": lambda v: round(v * 9 / 5 + 32, 1),
        "kalshi": True,
    },
    "total_precipitation_3h": {
        "nombre": "Precipitación 3h",
        "unidad": " mm",
        "modo": "max",
        "convert": lambda v: round(float(v), 2),
    },
    "dewpoint_2m": {
        "nombre": "Punto rocío",
        "unidad": "°F",
        "modo": "ahora",
        "convert": lambda v: round(v * 9 / 5 + 32, 1),
    },
    "wind_speed_10m": {
        "nombre": "Viento 10m",
        "unidad": " m/s",
        "modo": "ahora",
        "convert": lambda v: round(v, 1),
    },
    "total_cloud_cover": {
        "nombre": "Nubes totales",
        "unidad": "%",
        "modo": "ahora",
        "convert": lambda v: round(v * 100 if v <= 1 else v, 0),
    },
    "low_cloud_cover": {
        "nombre": "Nubes bajas",
        "unidad": "%",
        "modo": "max",
        "convert": lambda v: round(v * 100 if v <= 1 else v, 0),
    },
    "medium_cloud_cover": {
        "nombre": "Nubes medias",
        "unidad": "%",
        "modo": "max",
        "convert": lambda v: round(v * 100 if v <= 1 else v, 0),
    },
    "high_cloud_cover": {
        "nombre": "Nubes altas",
        "unidad": "%",
        "modo": "max",
        "convert": lambda v: round(v * 100 if v <= 1 else v, 0),
    },
    "cape": {
        "nombre": "CAPE",
        "unidad": " J/kg",
        "modo": "max",
        "convert": lambda v: round(float(v), 0),
    },
    "cloud_base_height": {
        "nombre": "Base nubes",
        "unidad": " m",
        "modo": "min",
        "convert": lambda v: round(float(v), 0),
    },
    "boundary_layer_height": {
        "nombre": "Capa límite",
        "unidad": " m",
        "modo": "max",
        "convert": lambda v: round(float(v), 0),
    },
    "skin_temperature": {
        "nombre": "Temp. superficie",
        "unidad": "°F",
        "modo": "max",
        "convert": lambda v: round(v * 9 / 5 + 32, 1),
    },
    "pressure_msl": {
        "nombre": "Presión MSL",
        "unidad": " hPa",
        "modo": "ahora",
        "convert": lambda v: round(float(v) / 100, 1),
    },
    "solar_radiation_downwards_3h": {
        "nombre": "Radiación solar 3h",
        "unidad": " J/m²",
        "modo": "max",
        "convert": lambda v: round(v, 0),
    },
    "short_wave_radiation": {
        "nombre": "Radiación onda corta",
        "unidad": " W/m²",
        "modo": "max",
        "convert": lambda v: round(v, 1),
    },
    "p_type": {
        "nombre": "Tipo precip.",
        "unidad": "",
        "modo": "ahora",
        "convert": lambda v: int(round(float(v))),
    },
}

P_TYPE_LABELS = {
    0: "Lluvia",
    1: "Lluvia helada",
    2: "Granizo",
    3: "Nieve",
}

MODELOS_COMPARAR = {
    "wm-6": "WeatherMesh-6 (IA)",
    "gfs": "GFS",
    "hrrr": "HRRR (alta resolución)",
}

PRECIP_UMBRALES = [
    ("gt_0p25mm", "≥0.25 mm"),
    ("gt_2p5mm", "≥2.5 mm"),
    ("gt_6mm", "≥6 mm"),
    ("gt_12p5mm", "≥12.5 mm"),
]


def _pct_prob(val):
    if val is None:
        return None
    v = float(val)
    return round(v * 100 if v <= 1 else v, 0)


def _dist_temp_f(dist):
    if not dist:
        return {}
    out = {}
    for k in ("mean", "p10", "p25", "p75", "p90"):
        if dist.get(k) is not None:
            out[k] = round(dist[k] * 9 / 5 + 32, 1)
    return out


def _elegir_extremo(puntos, meta):
    conv = meta["convert"]
    modo = meta.get("modo", "max")
    if modo == "min":
        return min(puntos, key=lambda p: conv(p["valor"]))
    if modo == "ahora":
        ahora = datetime.now(puntos[0]["dt"].tzinfo)
        return min(puntos, key=lambda p: abs((p["dt"] - ahora).total_seconds()))
    return max(puntos, key=lambda p: conv(p["valor"]))


def _analizar_desde_raw(var_key, raw, city):
    meta = VARIABLES_EXTRA[var_key]
    puntos = parse_points(raw, var_key, city)
    if not puntos:
        return None

    ahora = datetime.now(city["tz"])
    p_ahora = min(puntos, key=lambda p: abs((p["dt"] - ahora).total_seconds()))
    p_ext = _elegir_extremo(puntos, meta)

    return {
        "city": city,
        "meta": meta,
        "var": var_key,
        "puntos": puntos,
        "ahora": p_ahora,
        "extremo": p_ext,
        "init": raw.get("initialization_time", ""),
    }


def analizar_variable(var_key, city=None, raw=None):
    city = city or get_city(DEFAULT_CITY_ID)
    if raw is None:
        raw = fetch_variable(var_key, city=city)
    return _analizar_desde_raw(var_key, raw, city)


def analizar_variables(var_keys, city=None):
    city = city or get_city(DEFAULT_CITY_ID)
    raws, errors = fetch_variables(var_keys, city=city)
    analisis = {}
    for vk, raw in raws.items():
        a = _analizar_desde_raw(vk, raw, city)
        if a:
            analisis[vk] = a
    return analisis, errors


def msg_variable(var_key, city=None):
    a = analizar_variable(var_key, city=city)
    if not a:
        return "Sin datos."

    m = a["meta"]
    c = a["city"]["nombre"]
    conv = m["convert"]
    ah = conv(a["ahora"]["valor"])
    ex = conv(a["extremo"]["valor"])
    dist = a["extremo"].get("dist") or {}
    modo = m.get("modo", "max")

    if modo == "min":
        etiqueta = "Mínimo hoy"
    elif modo == "ahora":
        etiqueta = "Valor actual"
    else:
        etiqueta = "Máximo hoy"

    lineas = [
        f"<b>{m['nombre']} — {c}</b>",
        f"Ahora ({a['ahora']['hora']}): <b>{ah}{m['unidad']}</b>",
        f"{etiqueta} ({a['extremo']['hora']}): <b>{ex}{m['unidad']}</b>",
    ]

    if dist.get("p25") is not None and m["unidad"] == "°F":
        d = _dist_temp_f(dist)
        lineas.append(f"IA en extremo: {d.get('mean', ex)}{m['unidad']} ({d.get('p25')}–{d.get('p75')})")

    if m.get("kalshi"):
        lineas.append("\n<i>Ventana 3h — referencia directa para mercados de temperatura.</i>")

    return "\n".join(lineas)


def msg_min(city=None):
    return msg_variable("min_temperature_2m_3h", city=city)


def msg_lluvia(city=None):
    a = analizar_variable("total_precipitation_3h", city=city)
    if not a:
        return "Sin datos de precipitación."

    c = a["city"]["nombre"]
    conv = a["meta"]["convert"]
    pico = a["extremo"]
    mm = conv(pico["valor"])
    dist = pico.get("dist") or {}

    lineas = [
        f"<b>Precipitación — {c}</b>",
        f"Máx ventana 3h hoy: <b>{mm} mm</b> @ {pico['hora']}",
    ]

    probs = []
    for key, label in PRECIP_UMBRALES:
        p = _pct_prob(dist.get(key))
        if p is not None:
            probs.append(f"{label}: <b>{p}%</b>")
    if probs:
        lineas.append("Prob. IA en ese slot:\n" + " · ".join(probs))

    pm = [p for p in a["puntos"] if p["dt"].hour >= 12]
    if pm:
        max_pm = max(pm, key=lambda p: conv(p["valor"]))
        lineas.append(
            f"\nTarde ({max_pm['hora']}): {conv(max_pm['valor'])} mm"
            + (" — puede frenar el pico térmico" if conv(max_pm['valor']) >= 0.5 else "")
        )

    slots = []
    for p in a["puntos"]:
        d = p.get("dist") or {}
        p6 = _pct_prob(d.get("gt_6mm"))
        if conv(p["valor"]) >= 0.1 or (p6 and p6 >= 10):
            extra = f" P≥6mm:{p6}%" if p6 else ""
            slots.append(f"  {p['hora']}: {conv(p['valor'])} mm{extra}")
    if slots:
        lineas.append("\n<b>Slots con lluvia:</b>\n" + "\n".join(slots[:6]))

    return "\n".join(lineas)


def msg_tormenta(city=None):
    city = city or get_city(DEFAULT_CITY_ID)
    partes = [f"<b>Tormenta / convección — {city['nombre']}</b>"]

    try:
        datos, errors = analizar_variables(TORMENTA_VARS, city=city)
    except WindBorneError as e:
        return f"<b>Tormenta — {city['nombre']}</b>\n\n{_fmt_err(e)}"

    if errors and not datos:
        return (
            f"<b>Tormenta — {city['nombre']}</b>\n\n"
            f"WindBorne saturado. {_fmt_err(list(errors.values())[0])}"
        )

    cape_a = datos.get("cape")
    if cape_a:
        cv = cape_a["meta"]["convert"](cape_a["extremo"]["valor"])
        partes.append(f"CAPE máx: <b>{cv} J/kg</b> @ {cape_a['extremo']['hora']}")
        if cv >= 1000:
            partes.append("<i>CAPE alto → riesgo de tormenta fuerte</i>")
        elif cv >= 500:
            partes.append("<i>CAPE moderado → posible convección PM</i>")
    elif errors.get("cape"):
        partes.append(f"CAPE: {_fmt_err(errors['cape'])}")

    pt = datos.get("p_type")
    if pt:
        dist = pt["ahora"].get("dist") or pt["extremo"].get("dist") or {}
        probs = []
        for key, label in (
            ("p_rain", "Lluvia"),
            ("p_snow", "Nieve"),
            ("p_freezing_rain", "Lluvia helada"),
            ("p_ice_pellets", "Granizo"),
        ):
            p = _pct_prob(dist.get(key))
            if p is not None and p >= 5:
                probs.append(f"{label} {p}%")
        if probs:
            partes.append("Tipo precip. IA: " + " · ".join(probs))
        else:
            dom = P_TYPE_LABELS.get(pt["meta"]["convert"](pt["ahora"]["valor"]), "?")
            partes.append(f"Tipo dominante ahora: {dom}")

    for vk, etiqueta in (
        ("low_cloud_cover", "Nubes bajas"),
        ("cloud_base_height", "Base nubes"),
        ("boundary_layer_height", "Capa límite"),
    ):
        va = datos.get(vk)
        if not va:
            continue
        m = va["meta"]
        v = m["convert"](va["extremo"]["valor"])
        partes.append(f"{etiqueta}: <b>{v}{m['unidad']}</b> @ {va['extremo']['hora']}")

    if errors:
        partes.append(
            f"\n<i>Parcial ({len(datos)}/{len(TORMENTA_VARS)} vars). "
            "Reintenta en 1 min si falta algo.</i>"
        )

    partes.append(
        "\n<i>Tormenta PM + nubes bajas pueden bajar varios °F el máximo Kalshi.</i>"
    )
    return "\n".join(partes)


def msg_nubes(city=None):
    city = city or get_city(DEFAULT_CITY_ID)
    lineas = [f"<b>Nubes y radiación — {city['nombre']}</b>"]
    for vk in ("low_cloud_cover", "medium_cloud_cover", "high_cloud_cover", "total_cloud_cover"):
        try:
            a = analizar_variable(vk, city=city)
            if not a:
                continue
            m = a["meta"]
            v = m["convert"](a["ahora"]["valor"])
            lineas.append(f"{m['nombre']}: <b>{v}{m['unidad']}</b>")
        except Exception:
            pass
    for vk in ("short_wave_radiation", "solar_radiation_downwards_3h"):
        try:
            a = analizar_variable(vk, city=city)
            if not a:
                continue
            m = a["meta"]
            v = m["convert"](a["extremo"]["valor"])
            lineas.append(f"{m['nombre']} (pico): <b>{v}{m['unidad']}</b> @ {a['extremo']['hora']}")
        except Exception:
            pass
    return "\n".join(lineas) if len(lineas) > 1 else "Sin datos de nubes."


def comparar_3km(city=None):
    city = city or get_city(DEFAULT_CITY_ID)
    resultados = []
    for modelo, nombre in (("wm-6", "WM-6 (IA)"), ("wm-6-3km", "WM-6 3km")):
        try:
            raw = fetch_variable(
                "temperature_2m",
                city=city,
                model=modelo,
                with_dist=(modelo == "wm-6"),
            )
            pts = parse_points(raw, "temperature_2m", city)
            if not pts:
                resultados.append({"modelo": nombre, "error": "sin puntos"})
                continue
            max_c = max(p["valor"] for p in pts)
            max_f = round(max_c * 9 / 5 + 32, 1)
            hora = max(pts, key=lambda p: p["valor"])["hora"]
            dist = max(pts, key=lambda p: p["valor"]).get("dist") or {}
            resultados.append({
                "modelo": nombre,
                "max_f": max_f,
                "hora": hora,
                "p25": _dist_temp_f(dist).get("p25"),
                "p75": _dist_temp_f(dist).get("p75"),
            })
        except Exception as e:
            resultados.append({"modelo": nombre, "error": str(e)[:80]})

    gust_3km = None
    try:
        raw_g = fetch_variable("wind_gusts_10m", city=city, model="wm-6-3km", with_dist=False)
        pts_g = parse_points(raw_g, "wind_gusts_10m", city)
        if pts_g:
            gust_3km = round(max(p["valor"] for p in pts_g), 1)
    except Exception:
        pass

    lineas = [f"<b>WM-6 vs WM-6-3km — {city['nombre']}</b>"]
    for r in resultados:
        if "error" in r:
            lineas.append(f"• {r['modelo']}: {r['error']}")
        else:
            ia = ""
            if r.get("p25") is not None:
                ia = f" · IA {r['p25']}–{r['p75']}°F"
            lineas.append(f"• {r['modelo']}: <b>{r['max_f']}°F</b> ({r['hora']}){ia}")

    vals = [r["max_f"] for r in resultados if "max_f" in r]
    if len(vals) == 2:
        diff = round(vals[1] - vals[0], 1)
        lineas.append(f"\n3km − WM-6: <b>{diff:+.1f}°F</b>")
        if abs(diff) >= 1:
            lineas.append("<i>Divergencia ≥1°F — la resolución urbana importa.</i>")

    if gust_3km is not None:
        lineas.append(f"Ráfagas 3km (máx hoy): <b>{gust_3km} m/s</b>")

    return "\n".join(lineas)


def msg_clima_completo(city=None):
    partes = []
    for vk in ("dewpoint_2m", "wind_speed_10m", "total_cloud_cover", "pressure_msl", "skin_temperature"):
        try:
            a = analizar_variable(vk, city=city)
            if not a:
                continue
            m = a["meta"]
            v = m["convert"](a["ahora"]["valor"])
            partes.append(f"{m['nombre']}: <b>{v}{m['unidad']}</b>")
        except Exception as e:
            partes.append(f"{VARIABLES_EXTRA[vk]['nombre']}: error ({e})")

    if not partes:
        return "No se pudo obtener clima extra."

    c = (city or get_city(DEFAULT_CITY_ID))["nombre"]
    return f"<b>Condiciones actuales — {c} (WindBorne)</b>\n" + "\n".join(partes)


def comparar_modelos(city=None):
    city = city or get_city(DEFAULT_CITY_ID)
    resultados = []
    for modelo, nombre in MODELOS_COMPARAR.items():
        try:
            raw = fetch_variable("temperature_2m", city=city, model=modelo, with_dist=(modelo == "wm-6"))
            pts = parse_points(raw, "temperature_2m", city)
            if not pts:
                continue
            max_c = max(p["valor"] for p in pts)
            max_f = round(max_c * 9 / 5 + 32, 1)
            hora = max(pts, key=lambda p: p["valor"])["hora"]
            resultados.append({"modelo": nombre, "max_f": max_f, "hora": hora})
        except Exception as e:
            resultados.append({"modelo": nombre, "error": str(e)[:60]})

    if not resultados:
        return "No hay datos de modelos."

    lineas = [f"<b>Comparación de modelos — {city['nombre']}</b>"]
    for r in resultados:
        if "error" in r:
            lineas.append(f"• {r['modelo']}: {r['error']}")
        else:
            lineas.append(f"• {r['modelo']}: <b>{r['max_f']}°F</b> ({r['hora']})")

    vals = [r["max_f"] for r in resultados if "max_f" in r]
    if len(vals) >= 2:
        spread = round(max(vals) - min(vals), 1)
        lineas.append(f"\nDivergencia: {spread}°F")

    return "\n".join(lineas)


def lista_variables_api():
    import requests
    from config import WIND_KEY

    try:
        r = requests.get(
            "https://api.windbornesystems.com/forecasts/v1/wm-6/variables",
            headers={"Authorization": f"Bearer {WIND_KEY}"},
            timeout=20,
        )
        if r.ok:
            data = r.json()
            return data.get("sfc_variables", [])
    except Exception:
        pass
    return list(VARIABLES_EXTRA.keys())


def msg_vars(city=None):
    city = city or get_city(DEFAULT_CITY_ID)
    api_vars = lista_variables_api()
    integradas = list(VARIABLES_EXTRA.keys())
    lineas = [f"<b>Variables WM-6 — {city['nombre']}</b>"]
    lineas.append(f"<b>Integradas en bot ({len(integradas)}):</b>")
    for k in integradas:
        m = VARIABLES_EXTRA[k]
        lineas.append(f"• {m['nombre']} (<code>{k}</code>)")
    extra = [v for v in api_vars if v not in integradas]
    if extra:
        lineas.append(f"\n<b>API disponibles ({len(extra)} más):</b>")
        lineas.append(", ".join(f"<code>{v}</code>" for v in extra[:15]))
        if len(extra) > 15:
            lineas.append(f"… y {len(extra) - 15} más")
    return "\n".join(lineas)


def _resumen_desde_analisis(a):
    if not a:
        return None
    m = a["meta"]
    conv = m["convert"]
    return {
        "nombre": m["nombre"],
        "ahora": conv(a["ahora"]["valor"]),
        "extremo": conv(a["extremo"]["valor"]),
        "hora_extremo": a["extremo"]["hora"],
        "unidad": m["unidad"],
        "dist": a["extremo"].get("dist") or a["ahora"].get("dist"),
    }


def recolectar_reporte(city=None, analisis_temp=None):
    """Paquete completo de datos WindBorne para Gemini."""
    city = city or get_city(DEFAULT_CITY_ID)
    reporte = {
        "city": city,
        "temp": analisis_temp,
        "variables": {},
        "errors": {},
    }

    datos, errors = analizar_variables(REPORTE_VARS, city=city)
    reporte["errors"] = errors
    for vk, a in datos.items():
        r = _resumen_desde_analisis(a)
        if r:
            reporte["variables"][vk] = r

    return reporte


def texto_reporte_plano(reporte):
    """Versión sin HTML para el prompt de Gemini."""
    city = reporte["city"]
    lineas = [
        f"Ciudad: {city['nombre']} ({city.get('serie', 'USA')})",
        f"Estación METAR: {city.get('station', 'N/D')}",
        f"Coordenadas: {city['lat']}, {city['lon']}",
    ]

    a = reporte.get("temp")
    if a:
        p = a["pico"]
        d = _dist_temp_f(p.get("dist"))
        lineas += [
            "",
            "TEMPERATURA WM-6:",
            f"- {a['init_txt']}",
            f"- Ahora: {a['ahora']['temp_f']}F ({a['ahora']['hora']})",
            f"- Pico del dia: {p['temp_f']}F ({texto_hora(p)})",
            f"- Minimo del dia: {a['min_dia']}F",
            f"- Rango IA pico p25-p75: {d.get('p25', '?')}-{d.get('p75', '?')}F",
            f"- P>=97F en pico: {a['probs_pico'].get(97, 0)}%",
            f"- P>=98F en pico: {a['probs_pico'].get(98, 0)}%",
        ]
        m = a.get("metar")
        if m and m.get("temp_f") is not None:
            lineas.append(f"- METAR observado: {m['temp_f']}F (hace {m.get('age_min', '?')} min)")

    for vk, r in reporte.get("variables", {}).items():
        lineas.append(f"- {r['nombre']}: ahora {r['ahora']}{r['unidad']}, extremo {r['extremo']}{r['unidad']} @ {r['hora_extremo']}")
        dist = r.get("dist") or {}
        if vk == "total_precipitation_3h":
            probs = [f"{lbl} {_pct_prob(dist.get(k))}%" for k, lbl in PRECIP_UMBRALES if dist.get(k) is not None]
            if probs:
                lineas.append(f"  Prob precip: {', '.join(probs)}")
        if vk == "p_type":
            for key, lbl in (("p_rain", "lluvia"), ("p_snow", "nieve"), ("p_freezing_rain", "helada")):
                p = _pct_prob(dist.get(key))
                if p is not None:
                    lineas.append(f"  P({lbl}): {p}%")

    vars_ = reporte.get("variables", {})
    precip = vars_.get("total_precipitation_3h")
    if precip:
        dist = precip.get("dist") or {}
        probs = [f"{lbl} {_pct_prob(dist.get(k))}%" for k, lbl in PRECIP_UMBRALES if dist.get(k) is not None]
        lineas.append(
            f"\nIMPACTO LLUVIA: máx {precip['extremo']} mm @ {precip['hora_extremo']}"
            + (f" — probs: {', '.join(probs)}" if probs else "")
        )

    cape = vars_.get("cape")
    if cape:
        lineas.append(f"CAPE máx: {cape['extremo']} J/kg @ {cape['hora_extremo']}")

    ptype = vars_.get("p_type")
    if ptype:
        dist = ptype.get("dist") or {}
        tipos = []
        for key, lbl in (("p_rain", "lluvia"), ("p_snow", "nieve"), ("p_freezing_rain", "helada")):
            p = _pct_prob(dist.get(key))
            if p is not None and p >= 5:
                tipos.append(f"P({lbl})={p}%")
        if tipos:
            lineas.append("Tipo precip: " + ", ".join(tipos))

    nubes_bajas = vars_.get("low_cloud_cover")
    if nubes_bajas:
        lineas.append(
            f"Nubes bajas máx: {nubes_bajas['extremo']}% @ {nubes_bajas['hora_extremo']}"
        )

    return "\n".join(lineas)