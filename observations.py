import time
from datetime import datetime, timezone

import requests

from cities import KALSHI_CITIES
from utils import c_to_f as _c_to_f

METAR_TTL = 120
_metar_cache = {"ts": 0, "by_station": {}}

NWS_HEADERS = {
    "User-Agent": "WindBorneMonitor (alberto@example.com)",
    "Accept": "application/geo+json",
}
MAX_HOY_TTL = 300  # 5 min — el maximo real del dia solo puede subir
_max_hoy_cache = {}


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


def metar_max_hoy(station_id, tz, force=False):
    """Máximo REAL observado hoy (NWS, misma fuente contra la que liquida Kalshi).

    A diferencia de get_metar_obs (última lectura), esto recorre todas las
    observaciones de hoy y devuelve la más alta — comparable directo con el
    pico pronosticado por WM-6.
    """
    if not station_id:
        return None
    station_id = station_id.upper()
    clave = f"{station_id}:{datetime.now(tz).strftime('%Y-%m-%d')}"

    cached = _max_hoy_cache.get(clave)
    if not force and cached and time.time() - cached["ts"] < MAX_HOY_TTL:
        return cached["data"]

    try:
        inicio = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
        start = inicio.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        r = requests.get(
            f"https://api.weather.gov/stations/{station_id}/observations",
            params={"start": start, "limit": 500},
            headers=NWS_HEADERS,
            timeout=20,
        )
        r.raise_for_status()
        feats = r.json().get("features", [])
    except Exception as e:
        print(f"  [metar_max] {station_id}: {e}")
        return cached["data"] if cached else None

    mejor = None
    for f in feats:
        props = f.get("properties", {})
        temp_c = (props.get("temperature") or {}).get("value")
        ts = props.get("timestamp")
        if temp_c is None or not ts:
            continue
        if mejor is None or temp_c > mejor["temp_c"]:
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(tz)
                hora_local = dt.strftime("%H:%M")
            except ValueError:
                hora_local = ""
            mejor = {"temp_c": temp_c, "temp_f": _c_to_f(temp_c), "hora": hora_local}

    if mejor is None:
        return cached["data"] if cached else None

    resultado = {"temp_f": mejor["temp_f"], "hora": mejor["hora"], "station": station_id, "n_obs": len(feats)}
    _max_hoy_cache[clave] = {"ts": time.time(), "data": resultado}
    return resultado


def metar_linea(station_id):
    obs = get_metar_obs(station_id)
    if not obs:
        return "Observación METAR: sin datos"
    edad = f"hace {obs['age_min']} min" if obs["age_min"] is not None else "reciente"
    return (
        f"<b>Temp REAL ({obs['station']})</b>: {obs['temp_f']}°F "
        f"({edad} · {obs['raw_type']})"
    )