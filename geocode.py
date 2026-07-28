import re
import time

import requests

from zoneinfo import ZoneInfo

_GEO_CACHE = {}
_GEO_TTL = 86400
_STATION_CACHE = {}

NWS_HEADERS = {
    "User-Agent": "WindBorneMonitor (alberto@example.com)",
    "Accept": "application/geo+json",
}


def _slug(nombre):
    base = re.sub(r"[^a-z0-9]+", "_", nombre.lower()).strip("_")
    return f"custom_{base[:48]}"


def _cache_get(key):
    entry = _GEO_CACHE.get(key)
    if entry and time.time() - entry["ts"] < _GEO_TTL:
        return entry["city"]
    return None


def _cache_set(key, city):
    _GEO_CACHE[key] = {"city": city, "ts": time.time()}


def nearest_metar_station(lat, lon):
    clave = f"{round(lat, 3)},{round(lon, 3)}"
    if clave in _STATION_CACHE:
        return _STATION_CACHE[clave]
    try:
        r = requests.get(
            f"https://api.weather.gov/points/{lat},{lon}",
            headers=NWS_HEADERS,
            timeout=12,
        )
        r.raise_for_status()
        stations_url = r.json()["properties"].get("observationStations")
        if not stations_url:
            return None
        r2 = requests.get(stations_url, headers=NWS_HEADERS, timeout=12)
        r2.raise_for_status()
        features = r2.json().get("features") or []
        for feat in features:
            sid = feat.get("properties", {}).get("stationIdentifier")
            if sid:
                _STATION_CACHE[clave] = sid.upper()
                return sid.upper()
    except Exception as e:
        print(f"  [geocode] estacion METAR: {e}")
    return None


_STATE_HINTS = {
    "alabama": "Alabama", "al": "Alabama", "alaska": "Alaska", "ak": "Alaska",
    "arizona": "Arizona", "az": "Arizona", "arkansas": "Arkansas", "ar": "Arkansas",
    "california": "California", "ca": "California", "colorado": "Colorado", "co": "Colorado",
    "connecticut": "Connecticut", "ct": "Connecticut", "delaware": "Delaware", "de": "Delaware",
    "florida": "Florida", "fl": "Florida", "georgia": "Georgia", "ga": "Georgia",
    "hawaii": "Hawaii", "hi": "Hawaii", "idaho": "Idaho", "id": "Idaho",
    "illinois": "Illinois", "il": "Illinois", "indiana": "Indiana", "in": "Indiana",
    "iowa": "Iowa", "ia": "Iowa", "kansas": "Kansas", "ks": "Kansas",
    "kentucky": "Kentucky", "ky": "Kentucky", "louisiana": "Louisiana", "la": "Louisiana",
    "maine": "Maine", "me": "Maine", "maryland": "Maryland", "md": "Maryland",
    "massachusetts": "Massachusetts", "ma": "Massachusetts", "michigan": "Michigan", "mi": "Michigan",
    "minnesota": "Minnesota", "mn": "Minnesota", "mississippi": "Mississippi", "ms": "Mississippi",
    "missouri": "Missouri", "mo": "Missouri", "montana": "Montana", "mt": "Montana",
    "nebraska": "Nebraska", "ne": "Nebraska", "nevada": "Nevada", "nv": "Nevada",
    "new hampshire": "New Hampshire", "nh": "New Hampshire", "new jersey": "New Jersey", "nj": "New Jersey",
    "new mexico": "New Mexico", "nm": "New Mexico", "new york": "New York", "ny": "New York",
    "north carolina": "North Carolina", "nc": "North Carolina", "north dakota": "North Dakota", "nd": "North Dakota",
    "ohio": "Ohio", "oh": "Ohio", "oklahoma": "Oklahoma", "ok": "Oklahoma",
    "oregon": "Oregon", "or": "Oregon", "pennsylvania": "Pennsylvania", "pa": "Pennsylvania",
    "rhode island": "Rhode Island", "ri": "Rhode Island", "south carolina": "South Carolina", "sc": "South Carolina",
    "south dakota": "South Dakota", "sd": "South Dakota", "tennessee": "Tennessee", "tn": "Tennessee",
    "texas": "Texas", "tx": "Texas", "utah": "Utah", "ut": "Utah",
    "vermont": "Vermont", "vt": "Vermont", "virginia": "Virginia", "va": "Virginia",
    "washington": "Washington", "wa": "Washington", "west virginia": "West Virginia", "wv": "West Virginia",
    "wisconsin": "Wisconsin", "wi": "Wisconsin", "wyoming": "Wyoming", "wy": "Wyoming",
}


def _split_city_state(nombre):
    partes = nombre.strip().split()
    if len(partes) < 2:
        return nombre, None
    tail = " ".join(partes[-2:]).lower()
    tail1 = partes[-1].lower()
    estado = _STATE_HINTS.get(tail) or _STATE_HINTS.get(tail1)
    if estado:
        ciudad = " ".join(partes[:-2] if tail in _STATE_HINTS else partes[:-1]).strip()
        return ciudad or nombre, estado
    return nombre, None


def _buscar_geocode(nombre, estado=None):
    r = requests.get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={
            "name": nombre,
            "count": 10,
            "language": "en",
            "countryCode": "US",
        },
        timeout=12,
    )
    r.raise_for_status()
    results = r.json().get("results") or []
    if estado:
        filtrados = [
            c for c in results
            if (c.get("admin1") or "").lower() == estado.lower()
        ]
        if filtrados:
            results = filtrados
    return results


def _elegir_resultado(results):
    if not results:
        return None
    hit = results[0]
    for cand in results:
        feat = (cand.get("feature_code") or "").upper()
        if feat in ("PPL", "PPLA", "PPLA2", "PPLA3", "PPLA4", "PPLC", "PPLX", "ADM2"):
            hit = cand
            break
    return hit


def geocode_us_place(nombre):
    """Busca ciudad/pueblo USA por nombre (Open-Meteo geocoding)."""
    nombre = (nombre or "").strip()
    if not nombre or len(nombre) < 2:
        return None

    clave = nombre.lower()
    cached = _cache_get(clave)
    if cached:
        return cached

    try:
        ciudad, estado = _split_city_state(nombre)
        results = _buscar_geocode(ciudad, estado)
        if not results and ciudad != nombre:
            results = _buscar_geocode(nombre)
        hit = _elegir_resultado(results)
        if not hit:
            return None

        name = hit.get("name") or nombre
        admin1 = hit.get("admin1") or ""
        display = f"{name}, {admin1}" if admin1 else name
        tz_name = hit.get("timezone") or "America/New_York"
        lat = float(hit["latitude"])
        lon = float(hit["longitude"])
        station = nearest_metar_station(lat, lon)

        city = {
            "id": _slug(display),
            "nombre": name,
            "nombre_largo": display,
            "lat": lat,
            "lon": lon,
            "tz": ZoneInfo(tz_name),
            "serie": "USA",
            "station": station,
            "custom": True,
            "population": hit.get("population"),
        }
        _cache_set(clave, city)
        return city
    except Exception as e:
        print(f"  [geocode] ERROR '{nombre}': {e}")
        return None