import os
import requests
import json
from datetime import datetime, timezone

# --- Configuración ---
# La API key ya NO está escrita aquí. Antes de correr el script:
#
#   Si usas PowerShell (Windows, tu caso):
#     $env:WINDBORNE_API_KEY="tu_key_aqui"
#     python watch_nws.py
#
#   Si usas CMD (Windows):
#     set WINDBORNE_API_KEY=tu_key_aqui
#     python watch_nws.py
#
#   Si usas bash/zsh (Mac/Linux):
#     export WINDBORNE_API_KEY="tu_key_aqui"
#     python watch_nws.py
#
# Nota: esto solo dura mientras esa ventana de terminal esté abierta.
API_KEY = os.environ.get("WINDBORNE_API_KEY")

if not API_KEY:
    raise ValueError(
        "❌ Falta la variable de entorno WINDBORNE_API_KEY.\n"
        "En PowerShell corre esto ANTES del script:\n"
        '   $env:WINDBORNE_API_KEY="tu_key_aqui"'
    )

headers = {"Authorization": f"Bearer {API_KEY}"}

url = "https://api.windbornesystems.com/forecasts/v1/wm-6/point_forecast/interpolated"
params = {
    "coordinates": "39.7392,-104.9903",  # Denver
    "variable": "temperature_2m",
    "include_distribution": "true"
}


def c_to_f(celsius):
    """Convierte Celsius a Fahrenheit."""
    return celsius * 9 / 5 + 32


def encontrar_lista_de_puntos(data):
    """
    La API de WindBorne envuelve la lista de puntos bajo alguna clave
    de nivel superior (varía: 'points', 'forecast', 'data', 'predictions').
    Esta función la busca automáticamente probando las más comunes,
    y si no encuentra ninguna, busca cualquier lista de dicts que
    tenga 'time' y 'temperature_2m' adentro.
    """
    posibles_claves = ["points", "forecast", "predictions", "data", "results"]
    for clave in posibles_claves:
        valor = data.get(clave)
        if isinstance(valor, list) and valor:
            return valor

    # Fallback: buscar cualquier lista dentro del dict que parezca correcta
    for valor in data.values():
        if isinstance(valor, list) and valor and isinstance(valor[0], dict):
            if "time" in valor[0] and "temperature_2m" in valor[0]:
                return valor

    return []


def extraer_tarde(data):
    """
    Busca los puntos de pronóstico que caen en horario de tarde
    (12:00-18:00 hora local de Denver) y devuelve:
      - low, high de la temperatura puntual (temperature_2m)
      - el punto con el pronóstico más cercano al mediodía, incluyendo
        su distribución (mean, p10, p90, std) para ver la incertidumbre
      - la lista completa de puntos de la tarde encontrados

    Denver está en UTC-6 (horario de verano, MDT) en julio.
    """
    puntos = encontrar_lista_de_puntos(data)

    if not puntos:
        return None, None, None, []

    tarde = []
    for p in puntos:
        ts = p.get("time")
        temp = p.get("temperature_2m")
        if ts is None or temp is None:
            continue
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        hora_local = (dt.hour - 6) % 24
        if 12 <= hora_local <= 18:
            tarde.append({
                "hora_local": hora_local,
                "temperature_2m": temp,
                "distribution": p.get("distribution"),
                "time": ts,
            })

    if not tarde:
        return None, None, None, tarde

    valores = [pt["temperature_2m"] for pt in tarde]
    low = min(valores)
    high = max(valores)
    # Punto más cercano a las 15:00 hora local (mediodía-tarde), para
    # mostrar la distribución de incertidumbre de ese momento puntual
    punto_medio = min(tarde, key=lambda pt: abs(pt["hora_local"] - 15))

    return low, high, punto_medio, tarde


print("Consultando WindBorne para Denver...")
try:
    response = requests.get(url, headers=headers, params=params, timeout=15)
except requests.exceptions.RequestException as e:
    print(f"❌ Error de conexión: {e}")
    raise SystemExit(1)

if response.status_code == 200:
    data = response.json()
    print(f"\n✅ Éxito - {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    low, high, punto_medio, tarde = extraer_tarde(data)

    if low is not None and high is not None:
        print(f"\n🌤️  Tarde (12:00-18:00 hora Denver) - temperature_2m:")
        print(f"   Mínima: {low:.2f}  ({c_to_f(low):.1f}°F si es °C)")
        print(f"   Máxima: {high:.2f}  ({c_to_f(high):.1f}°F si es °C)")

        if punto_medio and punto_medio.get("distribution"):
            dist = punto_medio["distribution"]
            print(f"\n📊 Distribución en el punto más cercano a las 15:00 "
                  f"({punto_medio['time']}):")
            print(f"   mean: {dist.get('mean')}   std: {dist.get('std')}")
            print(f"   p10:  {dist.get('p10')}   p90: {dist.get('p90')}")
            print(f"   p05:  {dist.get('p05')}   p95: {dist.get('p95')}")
            print(f"   p01:  {dist.get('p01')}   p99: {dist.get('p99')}")
            print(f"   (rango p10-p90 = tu banda de confianza del 80%)")
    else:
        print("\n⚠️  No se encontraron puntos de pronóstico para la tarde.")
        print("   Revisa la estructura real del JSON abajo para ajustar")
        print("   las claves 'time'/'value' en la función extraer_tarde().")

    print("\n--- JSON crudo completo ---")
    print(json.dumps(data, indent=2))
else:
    print(f"❌ Error {response.status_code}")
    print(response.text)
