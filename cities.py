from zoneinfo import ZoneInfo

from geocode import geocode_us_place

DEFAULT_CITY_ID = "denver"
_CUSTOM_CITIES = {}

KALSHI_CITIES = {
    "nyc": {
        "nombre": "NYC",
        "nombre_largo": "New York",
        "lat": 40.7789,
        "lon": -73.9692,
        "tz": ZoneInfo("America/New_York"),
        "serie": "KXHIGHNY",
        "station": "KNYC",
    },
    "chicago": {
        "nombre": "Chicago",
        "nombre_largo": "Chicago",
        "lat": 41.7868,
        "lon": -87.7522,
        "tz": ZoneInfo("America/Chicago"),
        "serie": "KXHIGHCHI",
        "station": "KMDW",
    },
    "miami": {
        "nombre": "Miami",
        "nombre_largo": "Miami",
        "lat": 25.7959,
        "lon": -80.2870,
        "tz": ZoneInfo("America/New_York"),
        "serie": "KXHIGHMIA",
        "station": "KMIA",
    },
    "austin": {
        "nombre": "Austin",
        "nombre_largo": "Austin",
        "lat": 30.1975,
        "lon": -97.6664,
        "tz": ZoneInfo("America/Chicago"),
        "serie": "KXHIGHAUS",
        "station": "KAUS",
    },
    "la": {
        "nombre": "LA",
        "nombre_largo": "Los Angeles",
        "lat": 33.9425,
        "lon": -118.4081,
        "tz": ZoneInfo("America/Los_Angeles"),
        "serie": "KXHIGHLAX",
        "station": "KLAX",
    },
    "denver": {
        "nombre": "Denver",
        "nombre_largo": "Denver",
        "lat": 39.8561,
        "lon": -104.6737,
        "tz": ZoneInfo("America/Denver"),
        "serie": "KXHIGHDEN",
        "station": "KDEN",
    },
    "phoenix": {
        "nombre": "Phoenix",
        "nombre_largo": "Phoenix",
        "lat": 33.4342,
        "lon": -112.0116,
        "tz": ZoneInfo("America/Phoenix"),
        "serie": "KXHIGHTPHX",
        "station": "KPHX",
    },
    "philly": {
        "nombre": "Philly",
        "nombre_largo": "Philadelphia",
        "lat": 39.8719,
        "lon": -75.2411,
        "tz": ZoneInfo("America/New_York"),
        "serie": "KXHIGHPHIL",
        "station": "KPHL",
    },
    "houston": {
        "nombre": "Houston",
        "nombre_largo": "Houston",
        "lat": 29.6454,
        "lon": -95.2769,
        "tz": ZoneInfo("America/Chicago"),
        "serie": "KXHIGHTHOU",
        "station": "KHOU",
    },
    "minneapolis": {
        "nombre": "Minneapolis",
        "nombre_largo": "Minneapolis",
        "lat": 44.8848,
        "lon": -93.2223,
        "tz": ZoneInfo("America/Chicago"),
        "serie": "KXHIGHTMIN",
        "station": "KMSP",
    },
    "okc": {
        "nombre": "OKC",
        "nombre_largo": "Oklahoma City",
        "lat": 35.3931,
        "lon": -97.6007,
        "tz": ZoneInfo("America/Chicago"),
        "serie": "KXHIGHTOKC",
        "station": "KOKC",
    },
    "sf": {
        "nombre": "SF",
        "nombre_largo": "San Francisco",
        "lat": 37.6213,
        "lon": -122.3790,
        "tz": ZoneInfo("America/Los_Angeles"),
        "serie": "KXHIGHTSFO",
        "station": "KSFO",
    },
    "dc": {
        "nombre": "DC",
        "nombre_largo": "Washington DC",
        "lat": 38.8512,
        "lon": -77.0402,
        "tz": ZoneInfo("America/New_York"),
        "serie": "KXHIGHTDC",
        "station": "KDCA",
    },
    "boston": {
        "nombre": "Boston",
        "nombre_largo": "Boston",
        "lat": 42.3656,
        "lon": -71.0096,
        "tz": ZoneInfo("America/New_York"),
        "serie": "KXHIGHTBOS",
        "station": "KBOS",
    },
    "dallas": {
        "nombre": "Dallas",
        "nombre_largo": "Dallas",
        "lat": 32.8968,
        "lon": -97.0380,
        "tz": ZoneInfo("America/Chicago"),
        "serie": "KXHIGHTDAL",
        "station": "KDFW",
    },
    "seattle": {
        "nombre": "Seattle",
        "nombre_largo": "Seattle",
        "lat": 47.4502,
        "lon": -122.3088,
        "tz": ZoneInfo("America/Los_Angeles"),
        "serie": "KXHIGHTSEA",
        "station": "KSEA",
    },
    "vegas": {
        "nombre": "Las Vegas",
        "nombre_largo": "Las Vegas",
        "lat": 36.0840,
        "lon": -115.1537,
        "tz": ZoneInfo("America/Los_Angeles"),
        "serie": "KXHIGHTLV",
        "station": "KLAS",
    },
    "atlanta": {
        "nombre": "Atlanta",
        "nombre_largo": "Atlanta",
        "lat": 33.6407,
        "lon": -84.4277,
        "tz": ZoneInfo("America/New_York"),
        "serie": "KXHIGHTATL",
        "station": "KATL",
    },
    "sanantonio": {
        "nombre": "San Antonio",
        "nombre_largo": "San Antonio",
        "lat": 29.5337,
        "lon": -98.4698,
        "tz": ZoneInfo("America/Chicago"),
        "serie": "KXHIGHTSATX",
        "station": "KSAT",
    },
    "nola": {
        "nombre": "NOLA",
        "nombre_largo": "New Orleans",
        "lat": 29.9934,
        "lon": -90.2580,
        "tz": ZoneInfo("America/Chicago"),
        "serie": "KXHIGHTNOLA",
        "station": "KMSY",
    },
}

CITY_ALIASES = {
    "nyc": "nyc",
    "ny": "nyc",
    "newyork": "nyc",
    "new york": "nyc",
    "chicago": "chicago",
    "chi": "chicago",
    "miami": "miami",
    "mia": "miami",
    "austin": "austin",
    "aus": "austin",
    "la": "la",
    "lax": "la",
    "losangeles": "la",
    "los angeles": "la",
    "denver": "denver",
    "den": "denver",
    "phoenix": "phoenix",
    "phx": "phoenix",
    "philly": "philly",
    "philadelphia": "philly",
    "phil": "philly",
    "houston": "houston",
    "hou": "houston",
    "minneapolis": "minneapolis",
    "min": "minneapolis",
    "okc": "okc",
    "oklahomacity": "okc",
    "oklahoma city": "okc",
    "sf": "sf",
    "sfo": "sf",
    "sanfrancisco": "sf",
    "san francisco": "sf",
    "dc": "dc",
    "washington": "dc",
    "washingtondc": "dc",
    "washington dc": "dc",
    "boston": "boston",
    "bos": "boston",
    "dallas": "dallas",
    "dal": "dallas",
    "dfw": "dallas",
    "seattle": "seattle",
    "sea": "seattle",
    "vegas": "vegas",
    "lasvegas": "vegas",
    "las vegas": "vegas",
    "lv": "vegas",
    "atlanta": "atlanta",
    "atl": "atlanta",
    "sanantonio": "sanantonio",
    "san antonio": "sanantonio",
    "sat": "sanantonio",
    "satx": "sanantonio",
    "nola": "nola",
    "neworleans": "nola",
    "new orleans": "nola",
    "todas": "todas",
    "all": "todas",
    "usa": "todas",
}


def resolve_city(texto):
    if not texto:
        return None
    key = texto.lower().strip().replace("_", " ")
    key_compact = key.replace(" ", "")
    if key_compact in CITY_ALIASES:
        cid = CITY_ALIASES[key_compact]
        return None if cid == "todas" else cid
    if key in CITY_ALIASES:
        cid = CITY_ALIASES[key]
        return None if cid == "todas" else cid
    if key_compact in KALSHI_CITIES:
        return key_compact
    if key in KALSHI_CITIES:
        return key
    return None


def resolve_location(texto):
    """Kalshi (20 ciudades) o cualquier ciudad/pueblo USA por nombre."""
    cid = resolve_city(texto)
    if cid:
        return get_city(cid)
    city = geocode_us_place(texto)
    if city:
        _CUSTOM_CITIES[city["id"]] = city
    return city


def resolve_city_from_args(args):
    """Encuentra ciudad en args (soporta nombres de varias palabras)."""
    if not args:
        return None, args
    for n in range(len(args), 0, -1):
        fragment = " ".join(args[:n])
        city = resolve_location(fragment)
        if city:
            return city["id"], args[n:]
    return None, args


def get_city(city_id=None):
    cid = city_id or DEFAULT_CITY_ID
    if cid in KALSHI_CITIES:
        return {"id": cid, **KALSHI_CITIES[cid]}
    if cid in _CUSTOM_CITIES:
        return dict(_CUSTOM_CITIES[cid])
    raise ValueError(f"Ciudad desconocida: {city_id}")


def lista_ciudades_texto():
    lineas = ["<b>Ciudades Kalshi KXHIGH (20)</b>\n"]
    for cid, c in KALSHI_CITIES.items():
        lineas.append(
            f"• <b>{c['nombre']}</b> — {c['serie']} ({c['station']})\n"
            f"  /{cid} o /resumen {cid}"
        )
    lineas.append(
        "\n<b>Multi-ciudad Kalshi</b>\n"
        "/kalshi o /todas → resumen 20 ciudades (~2s)\n"
        "/refresh → forzar datos nuevos\n"
        "/ciudad denver → ciudad activa (monitor)\n"
        "/ciudad → ver ciudad activa\n\n"
        "<b>Cualquier ciudad USA</b>\n"
        "Escribe el nombre (ciudad o pueblo):\n"
        "• <code>boise</code> o <code>/pico boise</code>\n"
        "• <code>salt lake city</code>\n"
        "• <code>/resumen portland oregon</code>\n"
        "Busca por geocoding (Open-Meteo) + METAR cercano."
    )
    return "\n".join(lineas)