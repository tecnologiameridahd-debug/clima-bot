import os
import sys
from pathlib import Path

from cities import DEFAULT_CITY_ID, get_city

_default = get_city(DEFAULT_CITY_ID)
LAT, LON = _default["lat"], _default["lon"]
CIUDAD = _default["nombre"]
DENVER_TZ = _default["tz"]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
CACHE_DIR = os.path.join(BASE_DIR, "cache")
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)
LOG_CSV = os.path.join(LOG_DIR, f"wb_peak_{DEFAULT_CITY_ID}.csv")


def log_csv_path(city_id):
    return os.path.join(LOG_DIR, f"wb_peak_{city_id}.csv")


# --- Secrets: env → config_local → default ---
_LOCAL_WB = ""
_LOCAL_TG = ""
try:
    from config_local import WINDBORNE_API_KEY as _LOCAL_WB  # type: ignore
except ImportError:
    pass
try:
    from config_local import WB_TELEGRAM_TOKEN as _LOCAL_TG  # type: ignore
except ImportError:
    pass

WIND_KEY = (
    os.environ.get("WINDBORNE_API_KEY")
    or os.environ.get("WIND_KEY")
    or (_LOCAL_WB or "").strip()
)

TELEGRAM_TOKEN = (
    os.environ.get("WB_TELEGRAM_TOKEN")
    or os.environ.get("TELEGRAM_TOKEN")
    or (_LOCAL_TG or "").strip()
)

# Modelo principal (único con distribución IA para temperature_2m)
MODELO_PRINCIPAL = "wm-6"

# Umbrales Kalshi por defecto (se adaptan por ciudad en analysis)
UMBRALES_F = [95, 96, 97, 98, 99, 100]

# Monitor automático
INTERVALO_MONITOR = 1200  # 20 min (ahorra cuota)
UMBRAL_ALERTA_CAMBIO = 1.0  # °F

# Caché agresivo — free trial 2000 req se quema con prefetch corto
BATCH_CACHE_TTL = 900  # 15 min
PREFETCH_INTERVAL = 1800  # 30 min (antes 5 min → quemaba la trial)
PREFETCH_ENABLED = True  # False = solo on-demand
MIN_INTERVAL = 8  # segundos entre llamadas individuales
VAR_CACHE_TTL = 900  # 15 min
STALE_CACHE_TTL = 7200  # 2 h — si 429/cuota, servir caché vieja
DISK_CACHE_TTL = 7200  # persistir batch en disco
API_RETRY_DELAYS = [3]  # un reintento corto; el dashboard corta a 35s
CYCLONE_CACHE_TTL = 1800  # 30 min — ciclones se actualizan cada pocas horas

# Fallback Open-Meteo si WindBorne falla (sin cuota)
OPEN_METEO_ENABLED = True

# Doc técnica de la API WindBorne (Artifact publicado)
DOCS_URL = "https://claude.ai/code/artifact/2a759810-6d5e-4ede-9973-6e9176310d74"

# Gemini — reutiliza config_local del bot Denver si existe
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-pro")
GEMINI_FALLBACK_MODELS = ["gemini-2.5-flash", "gemini-2.5-flash-lite"]
GEMINI_CACHE_TTL = 900
GEMINI_TIMEOUT = 90
GEMINI_MIN_INTERVAL = 45

_weather_cfg = Path(__file__).resolve().parent.parent / "weather_data"
if _weather_cfg.is_dir() and str(_weather_cfg) not in sys.path:
    sys.path.insert(0, str(_weather_cfg))
try:
    from config_local import GEMINI_API_KEY as _LOCAL_GEMINI_KEY
    from config_local import GEMINI_FALLBACK_MODELS as _LOCAL_GEMINI_FB
    from config_local import GEMINI_MODEL as _LOCAL_GEMINI_MODEL

    if not GEMINI_API_KEY:
        GEMINI_API_KEY = _LOCAL_GEMINI_KEY
    if not os.environ.get("GEMINI_MODEL"):
        GEMINI_MODEL = _LOCAL_GEMINI_MODEL
    GEMINI_FALLBACK_MODELS = list(_LOCAL_GEMINI_FB)
except ImportError:
    pass