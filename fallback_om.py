"""
Fallback Open-Meteo (gratis, sin key) cuando WindBorne falla o está sin cuota.
No tiene distribución IA, pero da temp horaria y pico del día.
"""
from __future__ import annotations

from datetime import datetime

import requests

from cities import DEFAULT_CITY_ID, get_city

_OM_CACHE: dict = {}
_OM_TTL = 600  # 10 min


def fetch_open_meteo_day(city=None, force=False):
    city = city or get_city(DEFAULT_CITY_ID)
    key = f"om:{city['id']}"
    now = datetime.now().timestamp()
    if not force and key in _OM_CACHE and now - _OM_CACHE[key]["ts"] < _OM_TTL:
        return _OM_CACHE[key]["data"]

    r = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": city["lat"],
            "longitude": city["lon"],
            "hourly": "temperature_2m,precipitation_probability,cloud_cover",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
            "temperature_unit": "fahrenheit",
            "timezone": str(city["tz"]),
            "forecast_days": 2,
        },
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    _OM_CACHE[key] = {"ts": now, "data": data}
    return data


def puntos_hoy_om(city=None, force=False):
    """Lista de puntos horarios de HOY en formato compatible con analysis."""
    city = city or get_city(DEFAULT_CITY_ID)
    data = fetch_open_meteo_day(city=city, force=force)
    hourly = data.get("hourly") or {}
    times = hourly.get("time") or []
    temps = hourly.get("temperature_2m") or []
    hoy = datetime.now(city["tz"]).strftime("%Y-%m-%d")
    puntos = []
    for ts, t in zip(times, temps):
        if t is None or not str(ts).startswith(hoy):
            continue
        dt = datetime.fromisoformat(ts).replace(tzinfo=city["tz"])
        puntos.append(
            {
                "dt": dt,
                "hora": dt.strftime("%H:%M"),
                "valor": (t - 32) * 5 / 9,  # °C approx for compatibility
                "temp_c": round((t - 32) * 5 / 9, 2),
                "temp_f": round(float(t), 1),
                "dist": None,
                "raw": {},
                "fuente": "open-meteo",
            }
        )
    return sorted(puntos, key=lambda x: x["dt"])


def resumen_om(city=None, force=False):
    city = city or get_city(DEFAULT_CITY_ID)
    data = fetch_open_meteo_day(city=city, force=force)
    daily = data.get("daily") or {}
    hourly = data.get("hourly") or {}
    hoy = datetime.now(city["tz"]).strftime("%Y-%m-%d")
    dates = daily.get("time") or []
    maxs = daily.get("temperature_2m_max") or []
    mins = daily.get("temperature_2m_min") or []
    precip = daily.get("precipitation_sum") or []

    idx_hoy = 0
    idx_man = 1 if len(dates) > 1 else 0
    for i, d in enumerate(dates):
        if d == hoy:
            idx_hoy = i
            idx_man = i + 1 if i + 1 < len(dates) else i

    # temp "ahora" = hora más cercana
    times = hourly.get("time") or []
    temps = hourly.get("temperature_2m") or []
    ahora_f = None
    hora_ahora = ""
    now = datetime.now(city["tz"])
    best = None
    for ts, t in zip(times, temps):
        if t is None:
            continue
        try:
            dt = datetime.fromisoformat(ts).replace(tzinfo=city["tz"])
        except ValueError:
            continue
        if dt.strftime("%Y-%m-%d") != hoy:
            continue
        diff = abs((dt - now).total_seconds())
        if best is None or diff < best[0]:
            best = (diff, round(float(t), 1), dt.strftime("%H:%M"))
    if best:
        ahora_f, hora_ahora = best[1], best[2]

    return {
        "fuente": "open-meteo",
        "city": city,
        "ahora_f": ahora_f,
        "hora_ahora": hora_ahora,
        "pico_hoy": maxs[idx_hoy] if idx_hoy < len(maxs) else None,
        "min_hoy": mins[idx_hoy] if idx_hoy < len(mins) else None,
        "precip_hoy": precip[idx_hoy] if idx_hoy < len(precip) else None,
        "pico_manana": maxs[idx_man] if idx_man < len(maxs) else None,
        "min_manana": mins[idx_man] if idx_man < len(mins) else None,
        "fecha_manana": dates[idx_man] if idx_man < len(dates) else None,
        "fecha": hoy,
    }


def msg_manana(city=None):
    r = resumen_om(city=city)
    c = r["city"]
    return (
        f"<b>{c['nombre']} — MAÑANA (Open-Meteo)</b>\n"
        f"Fecha: {r.get('fecha_manana') or 'N/D'}\n"
        f"Pico: <b>{r.get('pico_manana')}°F</b>\n"
        f"Mín: <b>{r.get('min_manana')}°F</b>\n\n"
        f"<i>Fallback gratuito. Sin distribución IA WindBorne.</i>"
    )


def msg_fallback_resumen(city=None, error_wb: str = ""):
    r = resumen_om(city=city)
    c = r["city"]
    aviso = ""
    if error_wb:
        corto = error_wb[:180].replace("<", "")
        aviso = f"<i>WindBorne no disponible: {corto}</i>\n\n"
    return (
        f"{aviso}"
        f"<b>{c['nombre']} — Open-Meteo (fallback)</b>\n"
        f"Ahora: <b>{r.get('ahora_f')}°F</b> ({r.get('hora_ahora')})\n"
        f"Pico hoy: <b>{r.get('pico_hoy')}°F</b>\n"
        f"Mín hoy: <b>{r.get('min_hoy')}°F</b>\n"
        f"Mañana pico: <b>{r.get('pico_manana')}°F</b>\n\n"
        f"<i>Sin probs Kalshi IA (requiere WindBorne). "
        f"Si ves cuota 2000/2000, sube plan o espera nueva key.</i>"
    )
