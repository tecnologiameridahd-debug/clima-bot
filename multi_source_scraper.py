import requests
import csv
import os
from datetime import datetime

LAT, LON = 39.7385, -104.9849
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(BASE_DIR, "denver_multi_source_log.csv")

# ---- Tus keys ----
OWM_KEY = "18819894c047800d51b92a613e118074"
TOMORROW_KEY = "MafTDRryhJtviREcYZDoeg2O87JjacwZ"
VISUALCROSSING_KEY = "K4MZUMJDP8YMVNH6SHYY3VHCN"

NWS_HEADERS = {
    "User-Agent": "AlbertoWeatherBot (tu_email@ejemplo.com)",
    "Accept": "application/geo+json"
}

def get_nws():
    try:
        with open(os.path.join(BASE_DIR, "gridpoint.txt")) as f:
            grid_url = f.read().strip()
        r = requests.get(grid_url, headers=NWS_HEADERS, timeout=10)
        r.raise_for_status()
        props = r.json()["properties"]
        temp_c = props["maxTemperature"]["values"][0]["value"]  # HOY
        return round(temp_c * 9/5 + 32, 1)
    except Exception as e:
        return f"ERROR: {e}"

def get_openmeteo():
    try:
        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": LAT, "longitude": LON,
            "daily": "temperature_2m_max",
            "temperature_unit": "fahrenheit",
            "timezone": "America/Denver"
        }
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        return r.json()["daily"]["temperature_2m_max"][0]  # HOY
    except Exception as e:
        return f"ERROR: {e}"

def get_7timer():
    try:
        url = "http://www.7timer.info/bin/api.pl"
        params = {"lon": LON, "lat": LAT, "product": "civil", "output": "json"}
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()["dataseries"]
        temps_c = [d["temp2m"] for d in data[:8]]
        temp_f = max(temps_c) * 9/5 + 32
        return round(temp_f, 1)
    except Exception as e:
        return f"ERROR: {e}"

def get_openweathermap():
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        denver = ZoneInfo("America/Denver")
        url = "https://api.openweathermap.org/data/2.5/forecast"
        params = {"lat": LAT, "lon": LON, "appid": OWM_KEY, "units": "imperial"}
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        hoy = datetime.now(denver).strftime("%Y-%m-%d")
        temps = []
        for item in r.json()["list"]:
            dt = datetime.fromtimestamp(item["dt"], tz=denver)
            if dt.strftime("%Y-%m-%d") != hoy:
                continue
            main = item["main"]
            temps.append(float(main["temp"]))
            if main.get("temp_max") is not None:
                temps.append(float(main["temp_max"]))
        if not temps:
            return "ERROR: sin bloques de hoy"
        return round(max(temps), 1)
    except Exception as e:
        return f"ERROR: {e}"

def get_tomorrow_io():
    try:
        url = "https://api.tomorrow.io/v4/weather/forecast"
        params = {"location": f"{LAT},{LON}", "apikey": TOMORROW_KEY, "units": "imperial"}
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        daily = r.json()["timelines"]["daily"]
        return round(daily[0]["values"]["temperatureMax"], 1)  # HOY
    except Exception as e:
        return f"ERROR: {e}"

def get_visualcrossing():
    try:
        url = f"https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline/{LAT},{LON}"
        params = {"key": VISUALCROSSING_KEY, "unitGroup": "us", "include": "days", "contentType": "json"}
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        return round(data["days"][0]["tempmax"], 1)   # HOY - Corregido
    except Exception as e:
        return f"ERROR: {e}"

def fetch_all_and_log():
    row = {
        "scrape_time": datetime.now().isoformat(),
        "nws": get_nws(),
        "open_meteo": get_openmeteo(),
        "seven_timer": get_7timer(),
        "openweathermap": get_openweathermap(),
        "tomorrow_io": get_tomorrow_io(),
        "visual_crossing": get_visualcrossing(),
    }

    file_exists = os.path.exists(CSV_PATH)
    with open(CSV_PATH, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=row.keys())
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)

    print(f"[{row['scrape_time']}] Guardado:")
    for k, v in row.items():
        if k != "scrape_time":
            print(f"  {k}: {v}")

if __name__ == "__main__":
    fetch_all_and_log()
