import requests
import csv
import os
from datetime import datetime

HEADERS = {
    "User-Agent": "AlbertoWeatherBot (tecnologiameridahd@gmail.com)",
    "Accept": "application/geo+json"
}

LAT, LON = 39.7385, -104.9849
CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "denver_forecast_log.csv")
GRIDPOINT_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gridpoint.txt")

def get_grid_data_url():
    if os.path.exists(GRIDPOINT_CACHE):
        with open(GRIDPOINT_CACHE) as f:
            return f.read().strip()
    url = f"https://api.weather.gov/points/{LAT},{LON}"
    r = requests.get(url, headers=HEADERS, timeout=10)
    r.raise_for_status()
    grid_url = r.json()["properties"]["forecastGridData"]
    with open(GRIDPOINT_CACHE, "w") as f:
        f.write(grid_url)
    return grid_url

def fetch_and_log():
    grid_url = get_grid_data_url()
    r = requests.get(grid_url, headers=HEADERS, timeout=10)
    r.raise_for_status()
    props = r.json()["properties"]

    update_time = props["updateTime"]
    max_temps = props["maxTemperature"]["values"][:3]

    file_exists = os.path.exists(CSV_PATH)
    with open(CSV_PATH, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["scrape_time", "nws_update_time", "valid_time", "temp_f"])
        for entry in max_temps:
            temp_f = entry["value"] * 9/5 + 32
            writer.writerow([
                datetime.now().isoformat(),
                update_time,
                entry["validTime"],
                round(temp_f, 1)
            ])
    print(f"[{datetime.now()}] Guardado. NWS update: {update_time}")

if __name__ == "__main__":
    fetch_and_log()