import time
from datetime import datetime, timezone

import requests

from cities import KALSHI_CITIES
from utils import c_to_f as _c_to_f

METAR_TTL = 120
_metar_cache = {"ts": 0, "by_station": {}}


def fetch_all_metar(force=False):
    if not force and _metar_cache["by_station"] and time.time() - _metar_cache["ts"] < METAR_TTL:
        return _metar_cache["by_station"]

    ids = ",".join(c["station"] for c in KALSHI_CITIES.values())
    r = requests.get(
        "https://aviationweather.gov/api/data/metar",
        params={"ids": ids, "format": "json"},
        timeout=30,
    )
    r.raise_for_status()
    by_station = {row["icaoId"]: row for row in r.json()}
    _metar_cache["ts"] = time.time()
    _metar_cache["by_station"] = by_station
    return by_station


def _fetch_metar_station(station_id):
    station_id = station_id.upper()
    try:
        r = requests.get(
            "https://aviationweather.gov/api/data/metar",
            params={"ids": station_id, "format": "json"},
            timeout=15,
        )
        r.raise_for_status()
        items = r.json()
        if items:
            return items[0]
    except Exception as e:
        print(f"  [metar] {station_id}: {e}")
    return None


def get_metar_obs(station_id, force=False):
    if not station_id:
        return None
    data = fetch_all_metar(force=force)
    row = data.get(station_id.upper())
    if (not row or row.get("temp") is None) and station_id:
        row = _fetch_metar_station(station_id)
    if not row or row.get("temp") is None:
        return None

    report = row.get("reportTime") or row.get("receiptTime", "")
    age_min = None
    hora_local = ""
    if report:
        try:
            rt = datetime.fromisoformat(report.replace("Z", "+00:00"))
            age_min = int((datetime.now(timezone.utc) - rt).total_seconds() / 60)
            hora_local = rt.strftime("%H:%M UTC")
        except ValueError:
            pass

    return {
        "temp_f": _c_to_f(row["temp"]),
        "temp_c": row["temp"],
        "age_min": age_min,
        "hora": hora_local,
        "station": station_id.upper(),
        "raw_type": row.get("metarType", "METAR"),
    }


def metar_linea(station_id):
    obs = get_metar_obs(station_id)
    if not obs:
        return "Observación METAR: sin datos"
    edad = f"hace {obs['age_min']} min" if obs["age_min"] is not None else "reciente"
    return (
        f"<b>Temp REAL ({obs['station']})</b>: {obs['temp_f']}°F "
        f"({edad} · {obs['raw_type']})"
    )