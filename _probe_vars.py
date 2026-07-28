import requests
from api import _time_range
from cities import get_city
from config import WIND_KEY

city = get_city("denver")
min_t, max_t = _time_range(city)
url = "https://api.windbornesystems.com/forecasts/v1/wm-6/point_forecast/interpolated"
headers = {"Authorization": f"Bearer {WIND_KEY}"}
base = {
    "coordinates": f"{city['lat']},{city['lon']}",
    "min_forecast_time": min_t,
    "max_forecast_time": max_t,
    "include_distribution": "true",
}

tests = [
    "temperature_2m",
    "max_temperature_2m_3h",
    "dewpoint_2m",
    "wind_speed_10m",
    "wind_gusts_10m",
    "wind_direction_10m",
    "total_cloud_cover",
    "precipitation",
    "precipitation_1h",
    "relative_humidity_2m",
    "pressure_msl",
    "solar_radiation_downwards_3h",
    "short_wave_radiation",
    "cape",
    "visibility",
    "surface_pressure",
]

for v in tests:
    params = {**base, "variable": v}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=15)
        if r.status_code != 200:
            print(v, "HTTP", r.status_code)
            continue
        fc = r.json().get("forecasts") or []
        pts = fc[0] if fc and isinstance(fc[0], list) else fc
        n = len(pts) if isinstance(pts, list) else 0
        dist = n and isinstance(pts[0], dict) and bool(pts[0].get("distribution"))
        print(v, "OK", n, "pts", "dist" if dist else "no-dist")
    except Exception as e:
        print(v, "ERR", e)