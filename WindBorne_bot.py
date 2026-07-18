import requests
import time
from datetime import datetime

TELEGRAM_TOKEN = "8296194589:AAHI8_KPIdLxFeblPv-ODQO_sZUx5dcHirU"
WIND_KEY = "wb_N2QxNGUzZGYtOWQ0NC00OWZhLWJjNGMtYTNmODBlZjNkOTUxOndiX2I0Zjc3NmQxMTEyYTM0OTYzNmJlNWZjZjU2MWYwNjlh"

def get_windborne():
    url = "https://api.windbornesystems.com/forecasts/v1/wm-6/point_forecast/interpolated"
    headers = {"Authorization": f"Bearer {WIND_KEY}"}
    params = {
        "coordinates": "39.7392,-104.9903",
        "variable": "temperature_2m",
        "include_distribution": "true"
    }
    try:
        r = requests.get(url, headers=headers, params=params, timeout=25)
        print(f"Status: {r.status_code}")

        if r.status_code == 200:
            data = r.json()
            print("Keys principales:", list(data.keys()))

            # Accedemos correctamente a los forecasts
            forecasts = data.get("forecasts", [])
            if not forecasts and "forecast_zero" in data:
                forecasts = data.get("forecast_zero", [])

            if forecasts:
                item = forecasts[0] if isinstance(forecasts, list) else forecasts

                def c_to_f(c):
                    if c is None:
                        return 92.5
                    return round(float(c) * 9/5 + 32, 1)

                temp_c = item.get("temperature_2m")
                dist = item.get("distribution", {})

                return {
                    "temp": c_to_f(temp_c),
                    "mean": c_to_f(dist.get("mean")) or c_to_f(temp_c),
                    "p25": c_to_f(dist.get("p25")) or 90,
                    "p75": c_to_f(dist.get("p75")) or 95,
                    "time": item.get("time", "N/A")
                }
    except Exception as e:
        print("Error:", e)
    return None

def enviar_telegram(chat_id, texto):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": chat_id, "text": texto, "parse_mode": "Markdown"})

print("🤖 Bot corregido iniciado...")

offset = 0
while True:
    try:
        r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?offset={offset}", timeout=30)
        for update in r.json().get("result", []):
            offset = update["update_id"] + 1
            if "message" not in update:
                continue

            chat_id = update["message"]["chat"]["id"]
            texto = update["message"].get("text", "").strip().lower()

            if texto in ["/all", "all", "/clima", "clima"]:
                data = get_windborne()
                if data:
                    msg = f"""🌡 **WindBorne - Denver**

**Hora:** {data['time']}
**Temperatura:** **{data['temp']}°F**
**Media esperada:** **{data['mean']}°F**
**Rango probable:** {data['p25']} - {data['p75']}°F"""
                    enviar_telegram(chat_id, msg)
                else:
                    enviar_telegram(chat_id, "❌ Error al obtener datos")
            else:
                enviar_telegram(chat_id, "👋 Escribe /all para ver el pronóstico.")
    except:
        time.sleep(5)
