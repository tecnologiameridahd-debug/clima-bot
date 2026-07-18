import concurrent.futures
import csv
import io
import math
import os
import re
import sys
import requests
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ================= CONFIG =================
# Secrets: variables de entorno (Render) o config_local.py (PC). Nunca hardcodear.
def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default or "").strip()


def _local(name: str, default: str = ""):
    try:
        import config_local as _cfg  # type: ignore

        return getattr(_cfg, name, default)
    except ImportError:
        return default


TELEGRAM_TOKEN = (
    _env("TELEGRAM_TOKEN")
    or _env("CLIMA_TELEGRAM_TOKEN")
    or str(_local("TELEGRAM_TOKEN", "") or "")
)
# KDEN — estación oficial Kalshi KXHIGHDEN (misma que windborne_monitor)
LAT, LON = 39.8561, -104.6737
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(BASE_DIR, "denver_multi_source_log.csv")
DENVER_TZ = ZoneInfo("America/Denver")

TOMORROW_KEY = _env("TOMORROW_KEY") or str(_local("TOMORROW_KEY", "") or "")
WIND_KEY = (
    _env("WINDBORNE_API_KEY")
    or _env("WIND_KEY")
    or str(_local("WINDBORNE_API_KEY", "") or _local("WIND_KEY", "") or "")
)
VISUALCROSSING_KEY = _env("VISUALCROSSING_KEY") or str(
    _local("VISUALCROSSING_KEY", "") or ""
)
GEMINI_GOOGLE_SEARCH = _env("GEMINI_GOOGLE_SEARCH", "").lower() in ("1", "true", "yes")
if not _env("GEMINI_GOOGLE_SEARCH"):
    GEMINI_GOOGLE_SEARCH = bool(_local("GEMINI_GOOGLE_SEARCH", False))

# IA: Gemini y/o Grok (xAI) — keys en env o config_local.py
IA_PROVIDER = _env("IA_PROVIDER") or str(_local("IA_PROVIDER", "auto") or "auto")
GEMINI_API_KEY = _env("GEMINI_API_KEY") or str(_local("GEMINI_API_KEY", "") or "")
GEMINI_MODEL = _env("GEMINI_MODEL") or str(_local("GEMINI_MODEL", "gemini-2.5-pro") or "gemini-2.5-pro")
XAI_API_KEY = _env("XAI_API_KEY") or str(_local("XAI_API_KEY", "") or "")
GROK_MODEL = _env("GROK_MODEL") or str(
    _local("GROK_MODEL", "grok-4.20-0309-non-reasoning") or "grok-4.20-0309-non-reasoning"
)
_fb = _local("GEMINI_FALLBACK_MODELS", None)
GEMINI_FALLBACK_MODELS = (
    list(_fb) if _fb else ["gemini-2.5-flash", "gemini-2.5-flash-lite"]
)

NWS_HEADERS = {
    # NWS exige un User-Agent identificable (no de ejemplo genérico)
    "User-Agent": "GasRadarClimaBot/1.1 (contact@gasradarapp.com)",
    "Accept": "application/geo+json",
}

NOMBRES_FUENTE = {
    "kden": "KDEN observado",
    "nws_cli": "NWS CLI",
    "nws_forecast": "NWS pronostico",
    "open_meteo": "Open-Meteo",
    "seven_timer": "7Timer",
    "hrrr": "HRRR",
    "tomorrow_io": "Tomorrow.io",
    "gfs": "GFS",
    "windborne": "WindBorne IA",
}

# Fuentes traders + contexto IA (la mayoria no entran al promedio /all de 6 modelos)
NOMBRES_FUENTE_GEMINI = {
    # iem_observed: DESACTIVADO — max parcial de mediodía enreda el análisis
    "nam": "NAM (Open-Meteo)",
    "nws_zone": "NWS zone forecast",
    "metar_actual": "METAR KDEN (ahora)",
    "nws_hourly": "NWS hourly max",
    "openmeteo_hourly": "Open-Meteo hourly max",
    "ecmwf": "ECMWF IFS",
    "icon": "ICON DWD",
    "visual_crossing": "Visual Crossing",
}
# Sin IEM, sin CLI excerpt, sin KDEN
FUENTES_GEMINI_EXTRA = list(NOMBRES_FUENTE_GEMINI.keys())
# Solo pronósticos de traders
FUENTES_TRADERS = ["nam", "nws_zone"]
KDEN_ANALISIS_HOUR = 16  # solo /monitor

FUENTES_OFICIAL = []  # KDEN/CLI quitados del análisis (/all, gráficos, IA)
# WindBorne fuera del promedio: trial API expirado (401). Queda solo en /windborne.
# 8 fuentes activas: NWS + OM + 7Timer + HRRR + Tomorrow + GFS + NAM + NWS zone
FUENTES_MODELO = ["open_meteo", "seven_timer", "hrrr", "tomorrow_io", "gfs"]
FUENTES_CONSENSO_7TIMER = ["open_meteo", "hrrr", "tomorrow_io", "gfs"]
SEVEN_TIMER_OUTLIER_MAX = 2.0  # °F sobre mediana de otros modelos
FUENTES_PROMEDIO = list(FUENTES_MODELO)
FUENTES_ORDEN = ["nws_forecast"] + FUENTES_PROMEDIO
# Gráfico y /all: pronósticos activos (sin KDEN/CLI, sin WindBorne en el conteo)
FUENTES_UNIDAS = ["nws_forecast"] + FUENTES_MODELO + FUENTES_TRADERS
FUENTES_GRAFICO = list(FUENTES_UNIDAS)
FUENTES_ALL_BASE = ["nws_forecast"] + FUENTES_MODELO
TOTAL_FUENTES_ALL = len(FUENTES_ALL_BASE) + len(FUENTES_TRADERS)  # 8

FUENTES_GEMINI_ONLY = [
    f for f in FUENTES_GEMINI_EXTRA if f not in FUENTES_TRADERS
]

# observado = maximo real/medido | pronostico = maximo esperado del dia
TIPO_FUENTE = {
    "kden": "observado",
    "nws_cli": "observado",
    "nws_forecast": "pronostico",
    "open_meteo": "pronostico",
    "seven_timer": "pronostico",
    "hrrr": "pronostico",
    "tomorrow_io": "pronostico",
    "gfs": "pronostico",
    "windborne": "pronostico",
}

ETIQUETA_TIPO = {
    "observado": "max observado",
    "pronostico": "max pronosticado",
}

ETIQUETA_TIPO_CORTA = {
    "observado": "OBS",
    "pronostico": "PRON",
}

KDEN_STATION = "KDEN"
IEM_STATION = "DEN"  # IEM Mesonet usa DEN, no KDEN
UMBRALES_ALERTA = [95, 96, 97, 98]
MONITOR_INTERVAL = 120
OFFICIAL_CACHE_TTL = 90


def _tipo_fuente(fuente):
    return TIPO_FUENTE.get(fuente, "pronostico")


def _etiqueta_tipo(fuente, corta=False):
    tipo = _tipo_fuente(fuente)
    mapping = ETIQUETA_TIPO_CORTA if corta else ETIQUETA_TIPO
    return mapping[tipo]


def _linea_fuente(fuente, valor, extra=""):
    nombre = NOMBRES_FUENTE[fuente]
    if valor is None:
        return f"{nombre}: sin datos"
    return f"{nombre}: {valor}°F{extra}"


def _nombre_grafico(fuente):
    return NOMBRES_FUENTE[fuente]


# ================= TELEGRAM =================
def _teclado_clima():
    """Botones fijos — más fácil que memorizar comandos."""
    return {
        "keyboard": [
            [{"text": "🌤 /all"}, {"text": "📊 /grafico"}],
            [{"text": "🔔 /monitor on"}, {"text": "🔕 /monitor off"}],
            [{"text": "🤖 /gemini"}, {"text": "📡 /windborne"}],
            [{"text": "📋 /fuentes"}, {"text": "❓ /help"}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
    }


def enviar_telegram(chat_id, mensaje, parse_mode="HTML", con_teclado=True):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": mensaje}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if con_teclado:
        payload["reply_markup"] = _teclado_clima()
    try:
        r = requests.post(url, json=payload, timeout=15)
        if r.status_code != 200:
            print(f"Error enviando a Telegram ({r.status_code}): {r.text}")
            if parse_mode:
                payload2 = {"chat_id": chat_id, "text": mensaje}
                if con_teclado:
                    payload2["reply_markup"] = _teclado_clima()
                requests.post(url, json=payload2, timeout=15)
    except Exception as e:
        print(f"Excepción enviando a Telegram: {e}")


def enviar_telegram_largo(chat_id, mensaje, parse_mode=None):
    for i in range(0, len(mensaje), 4000):
        enviar_telegram(chat_id, mensaje[i : i + 4000], parse_mode=parse_mode)
        time.sleep(0.3)


def enviar_telegram_foto(chat_id, imagen_bytes, caption=""):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    try:
        r = requests.post(
            url,
            data={"chat_id": chat_id, "caption": caption[:1024]},
            files={"photo": ("grafico.png", imagen_bytes, "image/png")},
            timeout=30,
        )
        if r.status_code != 200:
            print(f"Error enviando foto ({r.status_code}): {r.text}")
    except Exception as e:
        print(f"Excepción enviando foto: {e}")


# ================= FECHA OBJETIVO =================
def fecha_objetivo_denver():
    """Día actual en hora de Denver (contrato Kalshi KXHIGHDEN)."""
    return datetime.now(DENVER_TZ).strftime("%Y-%m-%d")


def c_to_f(celsius):
    return round(float(celsius) * 9 / 5 + 32, 1)


KDEN_MIN_HOUR = 10  # antes de 10 MDT el max nocturno NO es el maximo del dia
KDEN_DAYLIGHT_START = 6  # ignorar lecturas 00:00–05:59 para el max diario


def _cache_clear(fuente):
    _SOURCE_CACHE.pop(fuente, None)


def _max_observado_fiable(obs, fuente="obs"):
    """Max observado fiable — excluye temps nocturnas de madrugada."""
    if not obs:
        return None

    now = datetime.now(DENVER_TZ)
    if isinstance(obs[0], (int, float)):
        pairs = [(now, float(t)) for t in obs]
    else:
        pairs = [(dt, float(t)) for dt, t in obs]

    if now.hour < KDEN_MIN_HOUR:
        ultima = max(p[0] for p in pairs).strftime("%H:%M")
        pico = max(t for _, t in pairs)
        print(
            f"  [{fuente}] omitido: {pico}F a las {ultima} es temp nocturna "
            f"(antes de {KDEN_MIN_HOUR}:00 MDT)"
        )
        return None

    pairs_day = [(dt, t) for dt, t in pairs if dt.hour >= KDEN_DAYLIGHT_START]
    if not pairs_day:
        print(f"  [{fuente}] omitido: sin lecturas desde {KDEN_DAYLIGHT_START}:00 MDT")
        return None

    if len(pairs_day) < 2 and now.hour < 14:
        print(
            f"  [{fuente}] omitido: {len(pairs_day)} lectura(s) diurna(s), "
            "espera mas observaciones"
        )
        return None

    return round(max(t for _, t in pairs_day), 1)


_WB_CACHE = {"analysis": None, "fetched_at": 0.0}
_WB_DAILY_PEAK = {"date": None, "pico_f": None, "pico_hora": None}
_SOURCE_CACHE = {}
_GEMINI_CACHE = {"texto": None, "fetched_at": 0.0}
CACHE_TTL = 300  # 5 min — modelos y pronosticos
GEMINI_CACHE_TTL = 900  # 15 min — evita repetir llamadas a la API
GEMINI_TIMEOUT = 60
GEMINI_MIN_INTERVAL = 45  # segundos minimos entre llamadas reales
_GEMINI_LOCK = threading.Lock()
_gemini_running = False

_monitor_chats = {}


# ================= FUENTES DE CLIMA =================
def get_kden_observed_max():
    """Maximo OBSERVADO hoy en KDEN — dato en vivo que usa Kalshi."""
    now = datetime.now(DENVER_TZ)
    if now.hour < KDEN_MIN_HOUR:
        _cache_clear("kden")
    else:
        cached = _cache_get("kden", OFFICIAL_CACHE_TTL)
        if cached is not None:
            return cached
    try:
        inicio_dia = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start_utc = inicio_dia.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
        r = requests.get(
            f"https://api.weather.gov/stations/{KDEN_STATION}/observations",
            params={"start": start_utc, "limit": 500},
            headers=NWS_HEADERS,
            timeout=15,
        )
        r.raise_for_status()
        hoy = fecha_objetivo_denver()
        obs = []
        for feat in r.json().get("features", []):
            props = feat["properties"]
            ts = props.get("timestamp")
            if not ts:
                continue
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(DENVER_TZ)
            if dt.strftime("%Y-%m-%d") != hoy:
                continue
            temp = props.get("temperature", {})
            if temp and temp.get("value") is not None:
                obs.append((dt, c_to_f(temp["value"])))
        valor = _max_observado_fiable(obs, "kden")
        if valor is None:
            _cache_clear("kden")
            return None
        _cache_set("kden", valor)
        return valor
    except Exception as e:
        print(f"  [kden] ERROR: {e}")
        return None


def _fetch_cli_info():
    cached = _cache_get("nws_cli_info", OFFICIAL_CACHE_TTL)
    if cached is not None:
        return cached
    try:
        from get_cli_report import (
            get_latest_cli_product_id,
            get_product_text,
            parse_max_temp_detail,
            parse_report_date,
            parse_valid_as_of,
        )

        product_id, issuance = get_latest_cli_product_id()
        if not product_id:
            return None
        text = get_product_text(product_id)
        det = parse_max_temp_detail(text)
        max_temp = det[0] if det else None
        max_hora = det[1] if det else None
        max_hour = det[2] if det else None
        valid_as_of, es_final = parse_valid_as_of(text)
        report_date = parse_report_date(text)
        objetivo = fecha_objetivo_denver()
        es_hoy = report_date == objetivo
        max_es_nocturno = max_hour is not None and max_hour < KDEN_MIN_HOUR
        if report_date and not es_hoy:
            print(
                f"  [nws_cli] reporte del {report_date}, hoy es {objetivo} — no usar como oficial"
            )
        elif es_hoy and max_es_nocturno and not es_final:
            print(
                f"  [nws_cli] omitido: {max_temp}F a las {max_hora} es preliminar nocturno "
                f"(no es el maximo del dia)"
            )
        usable = (
            max_temp is not None
            and es_hoy
            and (es_final or not max_es_nocturno)
        )
        info = {
            "max_f": float(max_temp) if usable else None,
            "max_f_reporte": float(max_temp) if max_temp is not None else None,
            "max_hora": max_hora,
            "max_es_nocturno": max_es_nocturno,
            "report_date": report_date,
            "es_hoy": es_hoy,
            "valid_as_of": valid_as_of,
            "es_final": es_final,
            "issuance": issuance,
        }
        _cache_set("nws_cli_info", info)
        return info
    except Exception as e:
        print(f"  [nws_cli] ERROR: {e}")
        return None


def get_nws_cli_max():
    info = _fetch_cli_info()
    return info["max_f"] if info and info.get("max_f") is not None else None


def _nws_grid_data_url():
    """Gridpoint NWS para KDEN (Kalshi), no el de gridpoint.txt downtown."""
    r = requests.get(
        f"https://api.weather.gov/points/{LAT},{LON}",
        headers=NWS_HEADERS,
        timeout=10,
    )
    r.raise_for_status()
    return r.json()["properties"]["forecastGridData"]


def get_nws_forecast():
    """Pronostico NWS diario en grid KDEN (NO es el dato de cierre Kalshi)."""
    cached = _cache_get("nws_forecast")
    if cached is not None:
        return cached
    try:
        r = requests.get(_nws_grid_data_url(), headers=NWS_HEADERS, timeout=10)
        r.raise_for_status()
        props = r.json()["properties"]
        objetivo = fecha_objetivo_denver()

        for entry in props["maxTemperature"]["values"]:
            valid_time_str = entry["validTime"].split("/")[0]
            valid_dt_utc = datetime.fromisoformat(valid_time_str)
            valid_dt_denver = valid_dt_utc.astimezone(DENVER_TZ)
            if valid_dt_denver.strftime("%Y-%m-%d") == objetivo:
                valor = c_to_f(entry["value"])
                _cache_set("nws_forecast", valor)
                return valor

        return None
    except Exception as e:
        print(f"  [nws_forecast] ERROR: {e}")
        return None


def _cli_es_final(cli_info=None):
    cli_info = cli_info if cli_info is not None else _fetch_cli_info()
    return bool(
        cli_info
        and cli_info.get("es_hoy")
        and cli_info.get("es_final")
        and cli_info.get("max_f") is not None
    )


def _obs_util_para_analisis(cli_info=None):
    """
    True solo cuando el max observado ya sirve para el análisis del día.
    Antes del pico (~16 MT) o sin CLI final, el KDEN parcial (ej. 88F a las 14h)
    NO debe usarse como 'oficial' ni restarse de los modelos.
    """
    if _cli_es_final(cli_info):
        return True
    return datetime.now(DENVER_TZ).hour >= KDEN_ANALISIS_HOUR


def get_kalshi_official_max(datos=None, para_analisis=False):
    """
    max(KDEN vivo, CLI).
    para_analisis=True → None si el obs aún es parcial (no jode promedios/IA).
    """
    if datos:
        vals = [datos.get("kden"), datos.get("nws_cli")]
    else:
        vals = [get_kden_observed_max(), get_nws_cli_max()]
    nums = [v for v in vals if v is not None]
    if not nums:
        return None
    valor = max(nums)
    if para_analisis and not _obs_util_para_analisis():
        return None
    return valor


def _open_meteo_daily_max(modelo=None, cache_key="open_meteo", timeout=20):
    """Max diario Open-Meteo con reintentos, hosts alternos y cache."""
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    params = {
        "latitude": LAT,
        "longitude": LON,
        "daily": "temperature_2m_max",
        "temperature_unit": "fahrenheit",
        "timezone": "America/Denver",
        "forecast_days": 3,
    }
    if modelo:
        params["models"] = modelo

    objetivo = fecha_objetivo_denver()
    hosts = (
        "https://api.open-meteo.com/v1/forecast",
        "https://api.open-meteo.com/v1/forecast",  # reintento mismo host
    )
    last_err = None
    for host in hosts:
        for intento in range(2):
            try:
                r = requests.get(
                    host,
                    params=params,
                    timeout=timeout,
                    headers={"User-Agent": "ClimaBotDenver/1.2"},
                )
                r.raise_for_status()
                data = r.json()["daily"]
                times = data.get("time") or []
                temps = data.get("temperature_2m_max") or []
                if objetivo not in times:
                    # Si no está hoy, usa el primer día disponible
                    if times and temps and temps[0] is not None:
                        valor = round(float(temps[0]), 1)
                        print(
                            f"  [{cache_key}] usando primer día API {times[0]}={valor}F"
                        )
                        _cache_set(cache_key, valor)
                        return valor
                    print(f"  [{cache_key}] fecha {objetivo} no esta en respuesta")
                    return None
                idx = times.index(objetivo)
                temp = temps[idx] if idx < len(temps) else None
                if temp is None:
                    print(f"  [{cache_key}] max de hoy es null en API")
                    return None
                valor = round(float(temp), 1)
                _cache_set(cache_key, valor)
                return valor
            except Exception as e:
                last_err = e
                time.sleep(0.4 * (intento + 1))
                continue

    print(f"  [{cache_key}] ERROR: {last_err}")
    stale = _SOURCE_CACHE.get(cache_key)
    if stale and stale.get("valor") is not None:
        print(f"  [{cache_key}] usando cache anterior: {stale['valor']}F")
        return stale["valor"]
    return None


def get_openmeteo():
    return _open_meteo_daily_max(cache_key="open_meteo", timeout=25)


def get_7timer():
    cached = _cache_get("seven_timer")
    if cached is not None:
        return cached
    # Render a veces bloquea http:// — probar https y http
    urls = (
        "https://www.7timer.info/bin/api.pl",
        "http://www.7timer.info/bin/api.pl",
    )
    last_err = None
    for url in urls:
        try:
            params = {"lon": LON, "lat": LAT, "product": "civil", "output": "json"}
            r = requests.get(url, params=params, timeout=18)
            r.raise_for_status()
            payload = r.json()
            series = payload.get("dataseries") or []
            if not series:
                continue

            objetivo = fecha_objetivo_denver()
            init_str = payload.get("init")
            if init_str:
                init_denver = datetime.strptime(init_str, "%Y%m%d%H").replace(
                    tzinfo=ZoneInfo("UTC")
                ).astimezone(DENVER_TZ)
            else:
                init_denver = datetime.now(DENVER_TZ)

            # timepoint = horas desde init del modelo
            temps_c = []
            for d in series:
                momento = init_denver + timedelta(hours=int(d.get("timepoint") or 0))
                if momento.strftime("%Y-%m-%d") == objetivo:
                    t = d.get("temp2m")
                    if t is not None:
                        temps_c.append(float(t))

            # Con 1+ puntos ya estimamos max del día (antes exigía 3 y de noche salía vacío)
            if not temps_c:
                print(f"  [seven_timer] sin puntos para {objetivo}")
                continue

            valor = c_to_f(max(temps_c))
            _cache_set("seven_timer", valor)
            return valor
        except Exception as e:
            last_err = e
            continue
    print(f"  [seven_timer] ERROR: {last_err}")
    stale = _SOURCE_CACHE.get("seven_timer")
    if stale and stale.get("valor") is not None:
        print(f"  [seven_timer] usando cache: {stale['valor']}F")
        return stale["valor"]
    return None


def _mediana(vals):
    orden = sorted(vals)
    n = len(orden)
    if not n:
        return None
    mid = n // 2
    if n % 2:
        return orden[mid]
    return (orden[mid - 1] + orden[mid]) / 2


def _filtrar_7timer_outlier(datos):
    """Marca 7Timer si es outlier, pero LO DEJA en el promedio (el user quiere 8 fuentes)."""
    st = datos.get("seven_timer")
    if st is None:
        return
    otros = [
        datos.get(f) for f in FUENTES_CONSENSO_7TIMER if datos.get(f) is not None
    ]
    if len(otros) < 2:
        return
    med = _mediana(otros)
    if st > med + 4.0:
        diff = round(st - med, 1)
        print(
            f"  [seven_timer] outlier +{diff}F vs mediana {med}F "
            f"(se mantiene en el promedio)"
        )
        datos["seven_timer_note"] = f"outlier +{diff}°F"


def _linea_modelo(fuente, datos):
    if fuente == "seven_timer" and datos.get("seven_timer") is None:
        raw = datos.get("seven_timer_raw")
        if raw is not None:
            return f"7Timer: omitido ({raw}°F, outlier vs consenso)"
    return _linea_fuente(fuente, datos.get(fuente))


def _trader_activo(traders, fuente):
    if not traders:
        return False
    return traders.get(fuente) is not None


def _fuente_all_activa(datos, fuente):
    if fuente in FUENTES_TRADERS:
        return _trader_activo(datos.get("traders") or {}, fuente)
    if datos.get(fuente) is not None:
        return True
    if fuente == "seven_timer" and datos.get("seven_timer_raw") is not None:
        return True
    return False


def _pie_cobertura_all(datos):
    activas = sum(1 for f in FUENTES_ALL_BASE + FUENTES_TRADERS if _fuente_all_activa(datos, f))
    n_modelos = len([f for f in FUENTES_MODELO if datos.get(f) is not None])
    n_traders = sum(1 for f in FUENTES_TRADERS if _trader_activo(datos.get("traders") or {}, f))
    n_trader_tot = len(FUENTES_TRADERS)
    n_mod_tot = len(FUENTES_MODELO)
    modelo_txt = f"{n_modelos}/{n_mod_tot} modelos"
    if datos.get("seven_timer_raw") is not None and datos.get("seven_timer") is None:
        modelo_txt += " (7Timer omitido)"
    extra_gemini = len(FUENTES_GEMINI_ONLY)
    return (
        f"📊 <i>{activas}/{TOTAL_FUENTES_ALL} fuentes · "
        f"{modelo_txt} · {n_traders}/{n_trader_tot} traders · +{extra_gemini} con /gemini</i>"
    )


def _open_meteo_model(modelo, cache_key):
    """Open-Meteo con modelo específico (HRRR, ECMWF, etc.)."""
    return _open_meteo_daily_max(modelo=modelo, cache_key=cache_key, timeout=15)


def get_hrrr():
    """HRRR — modelo alta resolución USA para Denver."""
    return _open_meteo_model("gfs_hrrr", "hrrr")


def get_gfs():
    """GFS — actualización cada 6h, pronóstico hora a hora (mejor que ECMWF para Denver)."""
    return _open_meteo_model("gfs_seamless", "gfs")


def get_ecmwf():
    """ECMWF IFS — modelo europeo global (solo contexto Gemini)."""
    return _open_meteo_model("ecmwf_ifs025", "ecmwf")


def get_icon():
    """ICON DWD — modelo aleman (solo contexto Gemini)."""
    return _open_meteo_model("icon_seamless", "icon")


def get_metar_actual():
    """Ultima observacion METAR en KDEN — temperatura actual, no maximo del dia."""
    cached = _cache_get("metar_actual", OFFICIAL_CACHE_TTL)
    if cached is not None:
        return cached
    try:
        r = requests.get(
            f"https://api.weather.gov/stations/{KDEN_STATION}/observations/latest",
            headers=NWS_HEADERS,
            timeout=10,
        )
        r.raise_for_status()
        props = r.json()["properties"]
        temp = props.get("temperature", {})
        if not temp or temp.get("value") is None:
            return None
        ts = props.get("timestamp")
        hora = "N/D"
        if ts:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(DENVER_TZ)
            hora = dt.strftime("%H:%M")
        resultado = {
            "temp_f": c_to_f(temp["value"]),
            "hora": hora,
            "descripcion": props.get("textDescription") or "",
        }
        _cache_set("metar_actual", resultado)
        return resultado
    except Exception as e:
        print(f"  [metar_actual] ERROR: {e}")
        return None


def get_nws_hourly_max():
    """Maximo del dia segun pronostico horario NWS (gridpoint KDEN)."""
    cached = _cache_get("nws_hourly")
    if cached is not None:
        return cached
    try:
        r = requests.get(
            f"https://api.weather.gov/points/{LAT},{LON}",
            headers=NWS_HEADERS,
            timeout=10,
        )
        r.raise_for_status()
        hourly_url = r.json()["properties"]["forecastHourly"]
        r2 = requests.get(hourly_url, headers=NWS_HEADERS, timeout=12)
        r2.raise_for_status()
        objetivo = fecha_objetivo_denver()
        temps = []
        for period in r2.json()["properties"]["periods"]:
            start = datetime.fromisoformat(period["startTime"]).astimezone(DENVER_TZ)
            if start.strftime("%Y-%m-%d") == objetivo:
                temps.append(float(period["temperature"]))
        if not temps:
            return None
        valor = round(max(temps), 1)
        _cache_set("nws_hourly", valor)
        return valor
    except Exception as e:
        print(f"  [nws_hourly] ERROR: {e}")
        return None


def get_openmeteo_hourly_max():
    """Maximo del dia segun pronostico horario Open-Meteo (best match)."""
    cached = _cache_get("openmeteo_hourly")
    if cached is not None:
        return cached
    try:
        params = {
            "latitude": LAT,
            "longitude": LON,
            "hourly": "temperature_2m",
            "temperature_unit": "fahrenheit",
            "timezone": "America/Denver",
            "forecast_days": 1,
        }
        r = requests.get("https://api.open-meteo.com/v1/forecast", params=params, timeout=12)
        r.raise_for_status()
        hourly = r.json()["hourly"]
        objetivo = fecha_objetivo_denver()
        temps = []
        for t_str, temp in zip(hourly["time"], hourly["temperature_2m"]):
            if t_str.startswith(objetivo) and temp is not None:
                temps.append(float(temp))
        if not temps:
            return None
        valor = round(max(temps), 1)
        _cache_set("openmeteo_hourly", valor)
        return valor
    except Exception as e:
        print(f"  [openmeteo_hourly] ERROR: {e}")
        return None


def get_visualcrossing():
    """Visual Crossing timeline — max de hoy (solo Gemini)."""
    if not VISUALCROSSING_KEY:
        return None
    cached = _cache_get("visual_crossing")
    if cached is not None:
        return cached
    try:
        url = (
            "https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/"
            f"timeline/{LAT},{LON}"
        )
        params = {
            "key": VISUALCROSSING_KEY,
            "unitGroup": "us",
            "include": "days",
            "contentType": "json",
        }
        r = requests.get(url, params=params, timeout=12)
        r.raise_for_status()
        valor = round(float(r.json()["days"][0]["tempmax"]), 1)
        _cache_set("visual_crossing", valor)
        return valor
    except Exception as e:
        print(f"  [visual_crossing] ERROR: {e}")
        return None


def get_iem_observed_max():
    """Max observado hoy en KDEN via IEM Mesonet (respaldo cruzado de NWS)."""
    now = datetime.now(DENVER_TZ)
    if now.hour < KDEN_MIN_HOUR:
        _cache_clear("iem_observed")
    else:
        cached = _cache_get("iem_observed", OFFICIAL_CACHE_TTL)
        if cached is not None:
            return cached
    try:
        ahora = datetime.now(DENVER_TZ)
        params = {
            "station": IEM_STATION,
            "data": "tmpf",
            "year1": ahora.year,
            "month1": ahora.month,
            "day1": ahora.day,
            "year2": ahora.year,
            "month2": ahora.month,
            "day2": ahora.day,
            "tz": "America/Denver",
            "format": "onlycomma",
            "latlon": "no",
            "elev": "no",
            "missing": "M",
            "report_type": ["1", "3", "4"],
        }
        r = requests.get(
            "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py",
            params=params,
            timeout=25,
        )
        r.raise_for_status()
        if "Too many requests" in r.text:
            print("  [iem_observed] rate limit IEM")
            return None
        obs = []
        for line in r.text.strip().splitlines():
            if not line or line.startswith("#") or line.startswith("station,"):
                continue
            parts = line.split(",")
            if len(parts) < 3:
                continue
            tmpf = parts[2].strip()
            if tmpf in ("M", "", "null"):
                continue
            try:
                tval = float(tmpf)
                ts_raw = parts[1].strip()
                dt = datetime.strptime(ts_raw, "%Y-%m-%d %H:%M").replace(tzinfo=DENVER_TZ)
                obs.append((dt, tval))
            except ValueError:
                continue
        if not obs:
            print("  [iem_observed] sin lecturas hoy en DEN")
            return None
        valor = _max_observado_fiable(obs, "iem_observed")
        if valor is None:
            _cache_clear("iem_observed")
            return None
        _cache_set("iem_observed", valor)
        return valor
    except Exception as e:
        print(f"  [iem_observed] ERROR: {e}")
        return None


def get_nam():
    """NAM — modelo corto plazo NCEP (~3 km CONUS)."""
    return _open_meteo_model("ncep_nam_conus", "nam")


def get_nws_zone_max():
    """Max del periodo diurno hoy en pronostico zone NWS (texto meteorologo)."""
    cached = _cache_get("nws_zone")
    if cached is not None:
        return cached
    try:
        r = requests.get(
            f"https://api.weather.gov/points/{LAT},{LON}",
            headers=NWS_HEADERS,
            timeout=15,
        )
        r.raise_for_status()
        forecast_url = r.json()["properties"]["forecast"]
        r2 = requests.get(forecast_url, headers=NWS_HEADERS, timeout=15)
        r2.raise_for_status()
        objetivo = fecha_objetivo_denver()
        temps = []
        temps_by_name = []
        for period in r2.json().get("properties", {}).get("periods") or []:
            if not period.get("isDaytime"):
                continue
            name = (period.get("name") or "").lower()
            start = datetime.fromisoformat(period["startTime"]).astimezone(DENVER_TZ)
            t = period.get("temperature")
            if t is None:
                continue
            t = float(t)
            # 1) Periodos diurnos que empiezan hoy
            if start.strftime("%Y-%m-%d") == objetivo:
                temps.append(t)
            # 2) Nombres típicos NWS
            if name in ("today", "this afternoon", "rest of today") or name.startswith(
                "today"
            ):
                temps_by_name.append(t)
        pool = temps or temps_by_name
        if not pool:
            # 3) Fallback: primer periodo diurno del forecast
            for period in r2.json().get("properties", {}).get("periods") or []:
                if period.get("isDaytime") and period.get("temperature") is not None:
                    pool = [float(period["temperature"])]
                    print("  [nws_zone] fallback primer diurno del forecast")
                    break
        if not pool:
            print("  [nws_zone] sin periodos diurnos")
            return None
        valor = round(max(pool), 1)
        _cache_set("nws_zone", valor)
        return valor
    except Exception as e:
        print(f"  [nws_zone] ERROR: {e}")
        stale = _SOURCE_CACHE.get("nws_zone")
        if stale and stale.get("valor") is not None:
            return stale["valor"]
        return None


def recolectar_fuentes_traders():
    """Traders: NAM + NWS zone (sin IEM — obs parcial confunde con el high del día)."""
    print("  [traders] Recolectando NAM + NWS zone...")
    return {
        "nam": get_nam(),
        "nws_zone": get_nws_zone_max(),
    }


def get_cli_excerpt():
    """Extracto del reporte CLI oficial (lineas clave para Gemini)."""
    cached = _cache_get("cli_excerpt", OFFICIAL_CACHE_TTL)
    if cached is not None:
        return cached
    try:
        from get_cli_report import get_latest_cli_product_id, get_product_text

        product_id, issuance = get_latest_cli_product_id()
        if not product_id:
            return None
        text = get_product_text(product_id)
        lineas = []
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            upper = line.upper()
            if any(
                kw in upper
                for kw in (
                    "CLIMATE REPORT",
                    "VALID TODAY",
                    "MAXIMUM",
                    "MINIMUM",
                    "AVERAGE",
                    "RECORD",
                    "PRECIPITATION",
                    "WIND",
                    "SKY COVER",
                )
            ):
                lineas.append(line)
        if not lineas:
            lineas = [ln.strip() for ln in text.splitlines() if ln.strip()][:12]
        excerpt = "\n".join(lineas[:18])
        if issuance:
            excerpt = f"(Emitido: {issuance})\n{excerpt}"
        _cache_set("cli_excerpt", excerpt)
        return excerpt
    except Exception as e:
        print(f"  [cli_excerpt] ERROR: {e}")
        return None


def _cache_get(fuente, ttl=None):
    entry = _SOURCE_CACHE.get(fuente)
    limite = ttl if ttl is not None else CACHE_TTL
    if entry and time.time() - entry["ts"] < limite:
        return entry["valor"]
    return None


def _cache_set(fuente, valor):
    _SOURCE_CACHE[fuente] = {"valor": valor, "ts": time.time()}


def get_tomorrow_io():
    cached = _cache_get("tomorrow_io")
    if cached is not None:
        return cached

    try:
        url = "https://api.tomorrow.io/v4/weather/forecast"
        params = {"location": f"{LAT},{LON}", "apikey": TOMORROW_KEY, "units": "imperial"}

        for intento in range(2):
            r = requests.get(url, params=params, timeout=10)
            if r.status_code == 429:
                print(f"  [tomorrow_io] Rate limit (429), intento {intento + 1}")
                if intento == 0:
                    time.sleep(3)
                    continue
                # Usar último valor guardado aunque el caché haya expirado
                stale = _SOURCE_CACHE.get("tomorrow_io")
                if stale and stale["valor"] is not None:
                    print(f"  [tomorrow_io] Usando caché anterior: {stale['valor']}°F")
                    return stale["valor"]
                return None
            r.raise_for_status()
            break

        daily = r.json()["timelines"]["daily"]
        objetivo = fecha_objetivo_denver()

        for entry in daily:
            entry_dt = datetime.fromisoformat(entry["time"].replace("Z", "+00:00"))
            entry_dt_denver = entry_dt.astimezone(DENVER_TZ)
            if entry_dt_denver.strftime("%Y-%m-%d") == objetivo:
                valor = round(entry["values"]["temperatureMax"], 1)
                _cache_set("tomorrow_io", valor)
                return valor

        return None
    except Exception as e:
        print(f"  [tomorrow_io] ERROR: {e}")
        stale = _SOURCE_CACHE.get("tomorrow_io")
        if stale and stale["valor"] is not None:
            return stale["valor"]
        return None


def _parse_wb_distribution(dist):
    if not dist:
        return None
    return {
        "mean": c_to_f(dist.get("mean")),
        "p10": c_to_f(dist.get("p10")),
        "p25": c_to_f(dist.get("p25")),
        "p75": c_to_f(dist.get("p75")),
        "p90": c_to_f(dist.get("p90")),
        "std_c": dist.get("std"),
    }


def _prob_desde_distribucion(dist, threshold_f):
    if not dist or dist.get("mean") is None:
        return None
    mean_c = dist["mean"]
    std_c = dist.get("std") or 0
    threshold_c = (threshold_f - 32) * 5 / 9
    if std_c <= 0:
        return 100 if c_to_f(mean_c) >= threshold_f else 0
    z = (threshold_c - mean_c) / std_c
    prob_below = 0.5 * (1 + math.erf(z / math.sqrt(2)))
    return round((1 - prob_below) * 100)


def _windborne_monitor_dir():
    return os.path.normpath(os.path.join(BASE_DIR, "..", "windborne_monitor"))


def _pico_log_windborne_hoy():
    """Maximo pico_f registrado hoy en windborne_monitor/logs (no se pierde al anochecer)."""
    try:
        log_path = os.path.join(_windborne_monitor_dir(), "logs", "wb_peak_denver.csv")
        if not os.path.exists(log_path):
            return None, None
        hoy = fecha_objetivo_denver()
        max_pico = None
        max_hora = None
        with open(log_path, newline="") as f:
            for row in csv.DictReader(f):
                ts = row.get("time", "")
                if not ts.startswith(hoy):
                    continue
                try:
                    pico = float(row["pico_f"])
                except (KeyError, TypeError, ValueError):
                    continue
                if max_pico is None or pico > max_pico:
                    max_pico = pico
                    max_hora = row.get("pico_hora") or None
        return max_pico, max_hora
    except Exception as e:
        print(f"  [windborne] log pico: {e}")
        return None, None


def _actualizar_pico_diario_wb(pico_f=None, pico_hora=None):
    """Conserva el maximo pronosticado del dia aunque la API ya no devuelva horas pasadas."""
    global _WB_DAILY_PEAK
    hoy = fecha_objetivo_denver()
    if _WB_DAILY_PEAK["date"] != hoy:
        _WB_DAILY_PEAK = {"date": hoy, "pico_f": None, "pico_hora": None}

    log_pico, log_hora = _pico_log_windborne_hoy()
    candidatos = []
    for val, hora in (
        (pico_f, pico_hora),
        (log_pico, log_hora),
        (_WB_DAILY_PEAK.get("pico_f"), _WB_DAILY_PEAK.get("pico_hora")),
    ):
        if val is not None:
            candidatos.append((float(val), hora))

    if not candidatos:
        return None, None

    mejor_val, mejor_hora = max(candidatos, key=lambda x: x[0])
    prev = _WB_DAILY_PEAK.get("pico_f")
    if prev is None or mejor_val >= prev:
        _WB_DAILY_PEAK["pico_f"] = round(mejor_val, 1)
        _WB_DAILY_PEAK["pico_hora"] = mejor_hora
        if prev is not None and mejor_val > prev:
            print(
                f"  [windborne] pico diario conservado: {prev}F -> {mejor_val}F "
                f"({mejor_hora})"
            )
    return _WB_DAILY_PEAK["pico_f"], _WB_DAILY_PEAK["pico_hora"]


def _pico_desde_monitor():
    """Mismo valor que windborne_monitor /pico, con pico maximo del dia."""
    pico_log, hora_log = _pico_log_windborne_hoy()
    try:
        mon = _windborne_monitor_dir()
        if mon not in sys.path:
            sys.path.insert(0, mon)
        from analysis import analizar as wb_analizar
        from cities import get_city

        a = wb_analizar(get_city("denver"))
        if a:
            pico_api = a["pico"]["temp_f"]
            hora_api = a["pico"]["hora"]
            pico, _ = _actualizar_pico_diario_wb(pico_api, hora_api)
            if pico_log is not None and (pico is None or pico_log > pico):
                pico, _ = _actualizar_pico_diario_wb(pico_log, hora_log)
            return pico
    except Exception as e:
        print(f"  [windborne] sync monitor: {e}")
    if pico_log is not None:
        pico, _ = _actualizar_pico_diario_wb(pico_log, hora_log)
        return pico
    return None


def _punto_windborne(point):
    dt = datetime.fromisoformat(point["time"].replace("Z", "+00:00")).astimezone(DENVER_TZ)
    dist_raw = point.get("distribution")
    return {
        "dt": dt,
        "hora": dt.strftime("%H:%M"),
        "temp": c_to_f(point.get("temperature_2m")),
        "dist": _parse_wb_distribution(dist_raw),
        "dist_raw": dist_raw,
    }


def _horas_a_decimal(dt):
    return dt.hour + dt.minute / 60.0


def _decimal_a_hora(valor):
    horas = int(valor) % 24
    minutos = int(round((valor - int(valor)) * 60)) % 60
    return f"{horas:02d}:{minutos:02d}"


def _estimar_hora_pico(puntos_hoy):
    """Interpola la hora del maximo entre slots de 3h (WindBorne no da hora exacta)."""
    if not puntos_hoy:
        return None, None
    orden = sorted(puntos_hoy, key=lambda p: p["dt"])
    pico = max(orden, key=lambda p: p["temp"])
    slot_hora = pico["hora"]
    idx = orden.index(pico)

    if idx == 0 or idx == len(orden) - 1:
        return slot_hora, slot_hora

    p0, p1, p2 = orden[idx - 1], orden[idx], orden[idx + 1]
    t0, t1, t2 = _horas_a_decimal(p0["dt"]), _horas_a_decimal(p1["dt"]), _horas_a_decimal(p2["dt"])
    y0, y1, y2 = p0["temp"], p1["temp"], p2["temp"]
    denom = (t0 - t1) * (t0 - t2) * (t1 - t2)
    if abs(denom) < 1e-9:
        return slot_hora, slot_hora

    a = (t2 * (y1 - y0) + t1 * (y0 - y2) + t0 * (y2 - y1)) / denom
    if abs(a) < 1e-9 or a >= 0:
        return slot_hora, slot_hora

    b = (t0 * t0 * (y1 - y2) + t1 * t1 * (y2 - y0) + t2 * t2 * (y0 - y1)) / denom
    t_vertex = max(t0, min(t2, -b / (2 * a)))
    return _decimal_a_hora(t_vertex), slot_hora


def _texto_hora_pico_wb(pico):
    hora = pico.get("hora") or "N/D"
    slot = pico.get("hora_slot")
    if slot and slot != hora:
        return f"~{hora} (slot {slot})"
    return f"~{hora}"


def _futuros_wb(puntos_hoy, punto_ahora, punto_pico):
    """Horas futuras sin repetir 'ahora' ni el slot del pico (ya tienen bloque propio)."""
    pico_dt = punto_pico.get("dt")
    return sorted(
        [
            p
            for p in puntos_hoy
            if p["dt"] > punto_ahora["dt"]
            and (pico_dt is None or p["dt"] != pico_dt)
        ],
        key=lambda p: p["dt"],
    )


def _linea_hora_wb(p):
    dist = p.get("dist") or {}
    p25, p75 = dist.get("p25"), dist.get("p75")
    if p25 is not None and p75 is not None:
        return f"🕒 {p['hora']} — med {p['temp']}°F · IA {p25}–{p75}°F"
    return f"🕒 {p['hora']} — {p['temp']}°F"


def _aplicar_pico_diario(analysis):
    """Si la API solo trae horas futuras, usa el maximo pronosticado visto hoy (ej. 95.5F)."""
    if not analysis:
        return analysis
    hora_pico = analysis["pico"].get("hora")
    pico_f, pico_hora = _actualizar_pico_diario_wb(
        analysis["pico"]["temp"], hora_pico
    )
    if pico_f is None:
        return analysis
    truncado = analysis.get("puntos_total", 0) < 8
    if pico_f > analysis["max_dia"] or truncado:
        if truncado and pico_f > analysis["max_dia"]:
            print(
                f"  [windborne] API truncada ({analysis['puntos_total']} pts) — "
                f"usando pico diario {pico_f}F"
            )
        analysis = {
            **analysis,
            "max_dia": pico_f,
            "pico": {
                **analysis["pico"],
                "temp": pico_f,
                "hora": pico_hora or analysis["pico"]["hora"],
                "hora_slot": analysis["pico"].get("hora_slot"),
            },
            "pico_diario": True,
        }
    return analysis


def fetch_windborne_analysis(max_age=90):
    global _WB_CACHE
    ahora_ts = time.time()
    if _WB_CACHE["analysis"] and ahora_ts - _WB_CACHE["fetched_at"] < max_age:
        return _aplicar_pico_diario(_WB_CACHE["analysis"])

    try:
        inicio_dia = datetime.now(DENVER_TZ).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        fin_dia = inicio_dia + timedelta(days=1) - timedelta(seconds=1)
        url = "https://api.windbornesystems.com/forecasts/v1/wm-6/point_forecast/interpolated"
        headers = {"Authorization": f"Bearer {WIND_KEY}"}
        params = {
            "coordinates": f"{LAT},{LON}",
            "variable": "temperature_2m",
            "include_distribution": "true",
            "min_forecast_time": inicio_dia.astimezone(ZoneInfo("UTC")).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "max_forecast_time": fin_dia.astimezone(ZoneInfo("UTC")).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
        }
        r = requests.get(url, headers=headers, params=params, timeout=25)
        if r.status_code == 429:
            body = (r.text or "").lower()
            if "quota exceeded" in body or "free trial" in body or "2000" in body:
                print(
                    "  [windborne] CUOTA FREE TRIAL AGOTADA (2000/2000). "
                    "Hay que subir de plan o poner otra API key."
                )
            else:
                print("  [windborne] Rate limit temporal (429). Espera 1–2 min.")
            # Si hay caché vieja, úsala
            if _WB_CACHE.get("analysis"):
                return _aplicar_pico_diario(_WB_CACHE["analysis"])
            return None
        if r.status_code == 401:
            print(
                "  [windborne] 401 Unauthorized — API key inválida o revocada. "
                "Revisa WINDBORNE_API_KEY en config_local.py o Render."
            )
            if _WB_CACHE.get("analysis"):
                return _aplicar_pico_diario(_WB_CACHE["analysis"])
            return None
        r.raise_for_status()
        data = r.json()

        forecasts_raw = data.get("forecasts", [])
        if not forecasts_raw:
            return None

        points_raw = (
            forecasts_raw[0] if isinstance(forecasts_raw[0], list) else forecasts_raw
        )
        objetivo = fecha_objetivo_denver()
        puntos_hoy = []
        for point in points_raw:
            parsed = _punto_windborne(point)
            if parsed["dt"].strftime("%Y-%m-%d") == objetivo:
                puntos_hoy.append(parsed)

        if not puntos_hoy:
            return None

        ahora_denver = datetime.now(DENVER_TZ)
        punto_ahora = min(
            puntos_hoy,
            key=lambda p: abs((p["dt"] - ahora_denver).total_seconds()),
        )
        punto_pico = max(puntos_hoy, key=lambda p: p["temp"])
        hora_est, hora_slot = _estimar_hora_pico(puntos_hoy)
        if hora_est:
            punto_pico = {
                **punto_pico,
                "hora": hora_est,
                "hora_slot": hora_slot,
            }
        futuros = _futuros_wb(puntos_hoy, punto_ahora, punto_pico)

        temps = [p["temp"] for p in puntos_hoy]
        probs_horas = {
            95: round(sum(1 for t in temps if t >= 95) / len(temps) * 100),
            96: round(sum(1 for t in temps if t >= 96) / len(temps) * 100),
            97: round(sum(1 for t in temps if t >= 97) / len(temps) * 100),
            98: round(sum(1 for t in temps if t >= 98) / len(temps) * 100),
        }
        probs_pico = {}
        if punto_pico.get("dist_raw"):
            for umbral in (95, 96, 97, 98):
                probs_pico[umbral] = _prob_desde_distribucion(
                    punto_pico["dist_raw"], umbral
                )

        init_time = data.get("initialization_time")
        init_txt = "N/D"
        if init_time:
            init_dt = datetime.fromisoformat(init_time.replace("Z", "+00:00")).astimezone(
                DENVER_TZ
            )
            mins = int((ahora_denver - init_dt).total_seconds() / 60)
            init_txt = (
                f"{init_dt.strftime('%H:%M')} Denver (hace {mins} min)"
                if mins >= 0
                else init_dt.strftime("%H:%M Denver")
            )

        analysis = {
            "objetivo": objetivo,
            "init_txt": init_txt,
            "ahora": punto_ahora,
            "pico": punto_pico,
            "futuros": futuros,
            "max_dia": round(max(temps), 1),
            "min_dia": round(min(temps), 1),
            "promedio": round(sum(temps) / len(temps), 1),
            "probs_horas": probs_horas,
            "probs_pico": probs_pico,
            "puntos_total": len(puntos_hoy),
        }
        analysis = _aplicar_pico_diario(analysis)
        _WB_CACHE = {"analysis": analysis, "fetched_at": ahora_ts}
        return analysis
    except Exception as e:
        print(f"  [windborne] ERROR: {e}")
        return None


def get_windborne():
    """Maximo PRONOSTICADO WindBorne del dia (conserva pico aunque KDEN ya lo haya superado)."""
    analysis = fetch_windborne_analysis()
    if analysis:
        return analysis["max_dia"]
    pico, _ = _actualizar_pico_diario_wb()
    return pico


def formato_windborne_corto(analysis):
    ahora = analysis["ahora"]
    pico = analysis["pico"]
    dist_ahora = ahora.get("dist") or {}
    dist_pico = pico.get("dist") or {}
    p25a = dist_ahora.get("p25", ahora["temp"])
    p75a = dist_ahora.get("p75", ahora["temp"])
    p25p = dist_pico.get("p25", pico["temp"])
    p75p = dist_pico.get("p75", pico["temp"])
    probs = analysis["probs_pico"] or analysis["probs_horas"]

    nota_pico = ""
    if analysis.get("pico_diario"):
        nota_pico = "\n<i>(pico max del dia — la API ya no muestra horas pasadas)</i>"
    elif pico.get("hora_slot") and pico.get("hora_slot") != pico.get("hora"):
        nota_pico = "\n<i>(hora estimada entre slots de 3h de la API)</i>"

    return f"""<b>🤖 WindBorne IA (tiempo real)</b>
Modelo actualizado: {analysis['init_txt']}

<b>Ahora ({ahora['hora']}):</b> {ahora['temp']}°F
Rango IA ahora: {p25a}–{p75a}°F

<b>Pico pronosticado hoy ({_texto_hora_pico_wb(pico)}):</b> {pico['temp']}°F{nota_pico}
Rango IA pico: {p25p}–{p75p}°F

<b>Prob. en el pico:</b>
• ≥ 97°F → <b>{probs.get(97, 0)}%</b>
• ≥ 98°F → <b>{probs.get(98, 0)}%</b>

Usa /windborne para el detalle hora por hora"""


def formato_windborne_completo(analysis):
    ahora = analysis["ahora"]
    pico = analysis["pico"]
    dist_ahora = ahora.get("dist") or {}
    dist_pico = pico.get("dist") or {}
    probs_h = analysis["probs_horas"]
    probs_p = analysis["probs_pico"]

    lineas_horas = [_linea_hora_wb(p) for p in analysis["futuros"][:8]]
    if not lineas_horas:
        lineas_horas.append("Sin mas horas futuras hoy (ahora y pico arriba).")

    bloque_probs = []
    if probs_p:
        bloque_probs.append("<b>Probabilidades en el PICO (distribución IA):</b>")
        for umbral in (95, 96, 97, 98):
            bloque_probs.append(f"• ≥ {umbral}°F → <b>{probs_p.get(umbral, 0)}%</b>")
    bloque_probs.append("")
    bloque_probs.append("<b>Probabilidades por horas restantes:</b>")
    for umbral in (95, 96, 97, 98):
        bloque_probs.append(f"• ≥ {umbral}°F → <b>{probs_h.get(umbral, 0)}%</b>")

    return f"""<b>🤖 WindBorne IA - Denver ({analysis['objetivo']})</b>
Modelo WM-6 actualizado: {analysis['init_txt']}
Puntos de hoy en el modelo: {analysis['puntos_total']}

<b>🌡 AHORA ({ahora['hora']} Denver)</b>
Temperatura: <b>{ahora['temp']}°F</b>
Rango IA ahora (p25–p75): {dist_ahora.get('p25', ahora['temp'])}–{dist_ahora.get('p75', ahora['temp'])}°F

<b>📈 PICO DEL DÍA ({_texto_hora_pico_wb(pico)})</b>
Máxima: <b>{pico['temp']}°F</b>
Rango probable (p25–p75): {dist_pico.get('p25', pico['temp'])}–{dist_pico.get('p75', pico['temp'])}°F
Rango 80% (p10–p90): {dist_pico.get('p10', pico['temp'])}–{dist_pico.get('p90', pico['temp'])}°F

<b>Resumen del día</b>
Máx: {analysis['max_dia']}°F | Mín: {analysis['min_dia']}°F | Prom: {analysis['promedio']}°F

<b>⏭ Próximas horas</b>
{chr(10).join(lineas_horas)}

{chr(10).join(bloque_probs)}"""


def get_windborne_detalle():
    analysis = fetch_windborne_analysis()
    if not analysis:
        return "No se pudieron obtener datos de WindBorne."
    return formato_windborne_completo(analysis)


# ================= GEMINI IA =================
def recolectar_contexto_gemini(datos=None):
    """Fuentes extra + traders para analisis IA."""
    print("  [gemini] Recolectando fuentes adicionales...")
    traders = (datos or {}).get("traders") or recolectar_fuentes_traders()
    return {
        **traders,
        "metar_actual": get_metar_actual(),
        "nws_hourly": get_nws_hourly_max(),
        "openmeteo_hourly": get_openmeteo_hourly_max(),
        "ecmwf": get_ecmwf(),
        "icon": get_icon(),
        "visual_crossing": get_visualcrossing(),
        # cli_excerpt / KDEN no se envían a la IA
    }


def _linea_contexto_gemini(fuente, valor):
    nombre = NOMBRES_FUENTE_GEMINI[fuente]
    if fuente == "metar_actual":
        if not valor:
            return f"- {nombre}: sin datos"
        desc = f" ({valor['descripcion']})" if valor.get("descripcion") else ""
        return f"- {nombre}: {valor['temp_f']}F a las {valor['hora']} Denver{desc}"
    if fuente == "cli_excerpt":
        if not valor:
            return f"- {nombre}: sin datos"
        return f"- {nombre}:\n{valor}"
    if valor is None:
        return f"- {nombre}: sin datos"
    return f"- {nombre}: {valor}F"


def _bloque_traders_resumen(traders):
    lineas = []
    for key in FUENTES_TRADERS:
        nombre = NOMBRES_FUENTE_GEMINI[key]
        val = traders.get(key)
        if val is not None:
            lineas.append(f"{nombre}: {val}°F")
        else:
            lineas.append(f"{nombre}: sin datos")
    return lineas


def formato_fuentes():
    """Lista de fuentes que consulta el bot, por categoria."""
    gemini_extra = "\n".join(f"  • {NOMBRES_FUENTE_GEMINI[f]}" for f in FUENTES_GEMINI_EXTRA)
    vc_ok = "configurada" if VISUALCROSSING_KEY else "sin API key"
    gsearch = "activo" if GEMINI_GOOGLE_SEARCH else "desactivado"
    ia_grok = "configurada" if XAI_API_KEY else "sin API key"
    ia_gemini = "configurada" if GEMINI_API_KEY else "sin API key"
    return (
        "<b>Fuentes del bot — Denver KXHIGHDEN</b>\n\n"
        "<b>/all = solo pronósticos</b> (KDEN y CLI fuera del promedio)\n\n"
        f"<b>ACTIVAS para /all</b> ({TOTAL_FUENTES_ALL} fuentes)\n"
        "  • NWS pronóstico diario\n"
        "  • Open-Meteo\n"
        "  • 7Timer\n"
        "  • HRRR · GFS · Tomorrow.io\n"
        "  • NAM · NWS zone (traders)\n\n"
        "<b>OPCIONAL</b>\n"
        "  • WindBorne IA — trial expirado (no entra al promedio)\n\n"
        "<b>MAS FUENTES IA</b> (/gemini)\n"
        f"{gemini_extra}\n"
        f"  • Visual Crossing: {vc_ok}\n"
        f"  • Google Search grounding: {gsearch}\n\n"
        f"<b>IA</b> (proveedor: {IA_PROVIDER})\n"
        f"  • Grok xAI: {ia_grok} ({GROK_MODEL})\n"
        f"  • Gemini: {ia_gemini} ({GEMINI_MODEL})\n"
        "  • auto = Grok primero, Gemini si falla\n\n"
        "/monitor on = alertas KDEN en vivo (opcional)\n"
        "Kalshi cierra con KDEN/CLI; este bot analiza solo forecasts."
    )


FUENTES_PROMEDIO_IA_EXTRA = [
    "nws_forecast",
    "nws_hourly",
    "nws_zone",
    "nam",
    "ecmwf",
    "icon",
    "visual_crossing",
]
# Sin cli_excerpt / iem / kden en IA
def _calcular_promedios_ia(datos, contexto=None):
    """Promedios numericos para el analisis IA (oficial excluido — no es pronostico)."""
    contexto = contexto or {}

    def _stats(vals):
        nums = [float(v) for v in vals if isinstance(v, (int, float))]
        if not nums:
            return {"prom": None, "n": 0, "min": None, "max": None}
        return {
            "prom": round(sum(nums) / len(nums), 1),
            "n": len(nums),
            "min": min(nums),
            "max": max(nums),
        }

    temps_modelo = [datos.get(f) for f in FUENTES_MODELO if datos.get(f) is not None]
    temps_extra = []
    for fuente in FUENTES_PROMEDIO_IA_EXTRA:
        if fuente == "nws_forecast":
            val = datos.get(fuente)
        else:
            val = contexto.get(fuente)
        if isinstance(val, (int, float)):
            temps_extra.append(val)
    modelos = _stats(temps_modelo)
    extra = _stats(temps_extra)
    todos = _stats(temps_modelo + temps_extra)
    # Nunca usar KDEN parcial (88 a mediodía) en promedios vs oficial
    # KDEN/CLI fuera del análisis — no promedios vs oficial
    return {
        "modelos": modelos,
        "extra": extra,
        "todos": todos,
        "oficial": None,
        "diff_vs_oficial": None,
        "obs_parcial": True,
        "kden_parcial": None,
    }


def _bloque_promedios_ia(promedios):
    m, e, t = promedios["modelos"], promedios["extra"], promedios["todos"]
    lineas = ["📊 Promedios calculados:"]
    if m["prom"] is not None:
        lineas.append(
            f"• 6 modelos /all: {m['prom']}°F ({m['n']} fuentes, {m['min']}–{m['max']}°F)"
        )
    if e["prom"] is not None:
        lineas.append(
            f"• Fuentes extra IA: {e['prom']}°F ({e['n']} fuentes, {e['min']}–{e['max']}°F)"
        )
    if t["prom"] is not None:
        lineas.append(
            f"• Todos los pronosticos: {t['prom']}°F ({t['n']} fuentes, {t['min']}–{t['max']}°F)"
        )
    lineas.append("• KDEN/CLI: no se usan en este análisis")
    return "\n".join(lineas)


def _armar_prompt_gemini(datos, wb, contexto=None):
    lineas_modelo = []
    for fuente in FUENTES_MODELO:
        valor = datos.get(fuente)
        if valor is not None:
            lineas_modelo.append(f"- {NOMBRES_FUENTE[fuente]}: {valor}F")
        else:
            lineas_modelo.append(f"- {NOMBRES_FUENTE[fuente]}: sin datos")
    nf = datos.get("nws_forecast")
    linea_forecast = f"- NWS forecast grid: {nf}F" if nf is not None else "- NWS forecast grid: sin datos"

    promedios = _calcular_promedios_ia(datos, contexto)
    m, e, t = promedios["modelos"], promedios["extra"], promedios["todos"]
    promedio = m["prom"] if m["prom"] is not None else "N/D"
    minimo = m["min"] if m["min"] is not None else "N/D"
    maximo = m["max"] if m["max"] is not None else "N/D"

    wb_txt = "Sin datos WindBorne."
    if wb:
        probs = wb.get("probs_pico") or wb.get("probs_horas") or {}
        dist_pico = wb["pico"].get("dist") or {}
        wb_txt = f"""
- Modelo actualizado: {wb['init_txt']}
- Temperatura ahora ({wb['ahora']['hora']}): {wb['ahora']['temp']}°F
- Pico del día ({_texto_hora_pico_wb(wb['pico'])}): {wb['pico']['temp']}°F
- Rango IA en pico (p25-p75): {dist_pico.get('p25', 'N/D')}–{dist_pico.get('p75', 'N/D')}°F
- Prob ≥97°F en pico: {probs.get(97, 0)}%
- Prob ≥98°F en pico: {probs.get(98, 0)}%
- Máx/Mín/Prom del día: {wb['max_dia']}/{wb['min_dia']}/{wb['promedio']}°F
"""

    contexto = contexto or {}
    lineas_extra = [_linea_contexto_gemini(f, contexto.get(f)) for f in FUENTES_GEMINI_EXTRA]
    prom_extra = e["prom"] if e["prom"] is not None else "N/D"
    prom_todos = t["prom"] if t["prom"] is not None else "N/D"

    busqueda_txt = (
        "Tienes acceso a Google Search: si hace falta, busca noticias o datos recientes "
        "sobre clima en Denver hoy (NWS, heat advisory) y citalas brevemente."
        if GEMINI_GOOGLE_SEARCH
        else "No uses busqueda web; basate solo en los datos proporcionados."
    )

    hora_denver = datetime.now(DENVER_TZ).strftime("%Y-%m-%d %H:%M %Z")
    return f"""Eres un analista experto en clima y mercados de predicción.
El usuario apuesta en Kalshi al contrato KXHIGHDEN (temperatura MÁXIMA diaria en Denver, CO).
REGLA: NO uses ni menciones KDEN observado ni NWS CLI. Solo pronósticos (modelos, NAM, zone, hourly).
Hoy es {hora_denver}.

{linea_forecast}

MODELOS PRINCIPALES:
{chr(10).join(lineas_modelo)}
- Promedio 6 modelos: {promedio}F (rango {minimo}–{maximo}F, {m['n']} fuentes)

FUENTES ADICIONALES (NAM, zone, hourly — SIN KDEN, CLI ni IEM):
{chr(10).join(lineas_extra)}
- Promedio fuentes extra: {prom_extra}F ({e['n']} fuentes)

PROMEDIOS GLOBALES:
- Promedio TODOS los pronosticos: {prom_todos}F ({t['n']} fuentes, rango {t['min'] if t['min'] is not None else 'N/D'}–{t['max'] if t['max'] is not None else 'N/D'}F)

WINDBORNE IA (tiempo real):
{wb_txt}

INSTRUCCION WEB: {busqueda_txt}

Responde en español, claro y directo (maximo 350 palabras):
1) Resume el high PRONOSTICADO del día (modelos + NAM + zone)
2) Incluye los 3 promedios: 6 modelos, fuentes extra, y TODOS los pronosticos
3) Señala discrepancias entre modelos (ej. zone vs media)
4) NO menciones KDEN, CLI ni IEM
5) NO recomiendes apostar YES o NO en umbrales 95/96/97/98
6) Los promedios son referencia, no decisión de apuesta

Texto plano para Telegram, sin asteriscos ni guiones bajos."""


def _modelos_gemini_intentos():
    modelos = [GEMINI_MODEL]
    for modelo in GEMINI_FALLBACK_MODELS:
        if modelo not in modelos:
            modelos.append(modelo)
    return modelos


def _mensaje_error_gemini_429(detalle=""):
    return (
        "⏳ Gemini sin quota disponible (error 429).\n\n"
        f"Modelo preferido: {GEMINI_MODEL}\n"
        "El plan gratis agotó el límite diario de ese modelo.\n\n"
        "Qué puedes hacer:\n"
        "• Espera 1–24 h y prueba /gemini de nuevo\n"
        "• Pon GEMINI_MODEL = \"gemini-2.5-flash-lite\" en config_local.py\n"
        "• Revisa tu uso en https://ai.dev/rate-limit\n\n"
        f"Detalle: {detalle[:250]}"
    )


def _extraer_texto_gemini(data):
    candidates = data.get("candidates", [])
    if not candidates:
        return None
    parts = candidates[0].get("content", {}).get("parts", [])
    textos = [p["text"].strip() for p in parts if p.get("text")]
    if not textos:
        return None
    return textos[-1] if len(textos) > 1 else textos[0]


def consultar_gemini(prompt):
    if not GEMINI_API_KEY:
        return None, (
            "❌ Falta GEMINI_API_KEY.\n\n"
            "1) Ve a https://aistudio.google.com/apikey\n"
            "2) Crea una API key gratis\n"
            "3) Edita config_local.py con tu key\n"
            "   o en PowerShell: $env:GEMINI_API_KEY=\"tu_key\""
        ), None

    modelos = _modelos_gemini_intentos()
    ultimo_error = ""
    usar_busqueda = GEMINI_GOOGLE_SEARCH

    try:
        for intento, modelo in enumerate(modelos):
            if intento > 0:
                espera = min(3 * intento, 10)
                print(f"  [gemini] Esperando {espera}s antes de probar {modelo}...")
                time.sleep(espera)
                # Google Search consume mas quota; omitir en fallbacks
                usar_busqueda = False

            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{modelo}:generateContent"
            )
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "maxOutputTokens": 900,
                    "temperature": 0.3,
                    "thinkingConfig": {"thinkingBudget": 0},
                },
            }
            if usar_busqueda:
                payload["tools"] = [{"google_search": {}}]

            r = requests.post(
                url,
                params={"key": GEMINI_API_KEY},
                json=payload,
                timeout=(5, GEMINI_TIMEOUT),
            )

            if r.status_code == 429:
                try:
                    msg = r.json().get("error", {}).get("message", r.text)
                except Exception:
                    msg = r.text
                ultimo_error = msg
                print(f"  [gemini] 429 en {modelo} — probando siguiente modelo...")
                continue

            if r.status_code != 200:
                ultimo_error = r.text[:300]
                print(f"  [gemini] Error {r.status_code} en {modelo}")
                continue

            texto = _extraer_texto_gemini(r.json())
            if not texto:
                ultimo_error = "respuesta vacía"
                continue
            if len(texto) < 120:
                ultimo_error = f"respuesta corta ({len(texto)} chars)"
                continue

            if modelo != GEMINI_MODEL:
                print(f"  [gemini] OK con fallback {modelo}")
            return texto, None, modelo

        if "429" in ultimo_error or "quota" in ultimo_error.lower():
            return None, _mensaje_error_gemini_429(ultimo_error), None
        return None, f"Error Gemini: {ultimo_error[:300]}", None
    except requests.exceptions.Timeout:
        return None, f"⏱ Gemini tardó más de {GEMINI_TIMEOUT}s. Intenta /gemini solo.", None
    except Exception as e:
        return None, f"Error consultando Gemini: {e}", None


def consultar_grok(prompt):
    if not XAI_API_KEY:
        return None, (
            "❌ Falta XAI_API_KEY.\n\n"
            "1) Ve a https://console.x.ai\n"
            "2) Crea una API key y carga créditos (~$5 alcanzan meses)\n"
            "3) Pon XAI_API_KEY en config_local.py"
        ), None

    try:
        r = requests.post(
            "https://api.x.ai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {XAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": GROK_MODEL,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Eres un analista de clima objetivo. Responde en español, "
                            "texto plano, sin markdown. No recomiendes apostar."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.3,
                "max_tokens": 900,
            },
            timeout=(5, GEMINI_TIMEOUT),
        )
        if r.status_code == 401:
            return None, "❌ XAI_API_KEY inválida. Revisa config_local.py", None
        if r.status_code == 429:
            return None, (
                "⏳ Grok rate limit (429). Espera unos segundos y prueba /gemini de nuevo."
            ), None
        if r.status_code != 200:
            return None, f"Error Grok ({r.status_code}): {r.text[:300]}", None

        data = r.json()
        choices = data.get("choices", [])
        if not choices:
            return None, "Grok no devolvió respuesta.", None
        texto = (choices[0].get("message", {}).get("content") or "").strip()
        if len(texto) < 120:
            return None, f"Respuesta corta de Grok ({len(texto)} chars).", None
        return texto, None, GROK_MODEL
    except requests.exceptions.Timeout:
        return None, f"⏱ Grok tardó más de {GEMINI_TIMEOUT}s.", None
    except Exception as e:
        return None, f"Error consultando Grok: {e}", None


def consultar_ia(prompt):
    """Enruta al proveedor configurado. auto = Grok primero si hay key, luego Gemini."""
    proveedor = (IA_PROVIDER or "auto").lower()

    if proveedor == "grok":
        return consultar_grok(prompt)
    if proveedor == "gemini":
        return consultar_gemini(prompt)

    # auto
    if XAI_API_KEY:
        texto, error, modelo = consultar_grok(prompt)
        if texto:
            return texto, None, f"grok/{modelo}"
        print(f"  [ia] Grok falló, probando Gemini: {error}")

    texto, error, modelo = consultar_gemini(prompt)
    if texto:
        return texto, None, f"gemini/{modelo}"

    if XAI_API_KEY and not texto:
        return None, error, None
    if GEMINI_API_KEY:
        return None, error, None
    return (
        None,
        "❌ Sin IA configurada. Añade XAI_API_KEY o GEMINI_API_KEY en config_local.py",
        None,
    )


def analisis_gemini(datos=None, wb=None):
    global _GEMINI_CACHE
    cache_key = f"{fecha_objetivo_denver()}_{datetime.now(DENVER_TZ).strftime('%H')}"
    if (
        _GEMINI_CACHE["texto"]
        and _GEMINI_CACHE.get("key") == cache_key
        and time.time() - _GEMINI_CACHE["fetched_at"] < GEMINI_CACHE_TTL
    ):
        return _GEMINI_CACHE["texto"]

    ahora = time.time()
    if (
        _GEMINI_CACHE.get("texto")
        and ahora - _GEMINI_CACHE.get("fetched_at", 0) < GEMINI_MIN_INTERVAL
    ):
        return _GEMINI_CACHE["texto"] + "\n\n(reciente — espera antes de repetir)"

    if not _GEMINI_LOCK.acquire(blocking=False):
        return "⏳ Ya hay un análisis Gemini en curso. Espera un momento."

    try:
        datos = datos or recolectar_datos()
        wb = wb or fetch_windborne_analysis()
        contexto = recolectar_contexto_gemini(datos)
        promedios = _calcular_promedios_ia(datos, contexto)
        prompt = _armar_prompt_gemini(datos, wb, contexto)
        texto, error, modelo_usado = consultar_ia(prompt)
        if error:
            return error
        proveedor = (modelo_usado or "IA").split("/")[0].capitalize()
        modelo_txt = modelo_usado or GEMINI_MODEL
        bloque_prom = _bloque_promedios_ia(promedios)
        resultado = (
            f"🧠 Análisis {proveedor} ({modelo_txt})\n\n{texto}\n\n{bloque_prom}"
        )
        _GEMINI_CACHE = {
            "texto": resultado,
            "key": cache_key,
            "fetched_at": time.time(),
        }
        return resultado
    finally:
        _GEMINI_LOCK.release()


def _gemini_en_background(chat_id, datos, wb):
    global _gemini_running
    try:
        resultado = analisis_gemini(datos, wb)
        print(f"Gemini listo ({len(resultado)} chars) → chat {chat_id}")
        enviar_telegram_largo(chat_id, resultado, parse_mode=None)
    except Exception as e:
        print(f"Error Gemini background: {e}")
        enviar_telegram(chat_id, f"❌ Error Gemini: {e}", parse_mode=None)
    finally:
        _gemini_running = False


def lanzar_gemini_async(chat_id, datos=None, wb=None):
    global _gemini_running
    if _gemini_running:
        enviar_telegram(
            chat_id,
            "⏳ Gemini ya está analizando. Espera a que termine.",
            parse_mode=None,
        )
        return
    _gemini_running = True
    extra_n = len(FUENTES_GEMINI_EXTRA)
    busqueda = " + Google Search" if GEMINI_GOOGLE_SEARCH else ""
    enviar_telegram(
        chat_id,
        f"🧠 IA analizando {extra_n} fuentes ({IA_PROVIDER}) (~{GEMINI_TIMEOUT}s máx)...",
        parse_mode=None,
    )
    threading.Thread(
        target=_gemini_en_background,
        args=(chat_id, datos, wb),
        daemon=True,
    ).start()


# ================= RECOLECTAR Y ARMAR RESUMEN =================
def _safe_call(name, fn):
    try:
        return fn()
    except Exception as e:
        print(f"  [{name}] EXCEPTION: {type(e).__name__}: {e}")
        return None


def _rellenar_fallos(datos):
    """
    Si una fuente falla, rellena con la más cercana para no dejar 'sin datos'.
    En Render, Open-Meteo a veces bloquea: usamos NWS / Tomorrow como base.
    """
    fb = {}
    om = datos.get("open_meteo")
    nws = datos.get("nws_forecast")
    tom = datos.get("tomorrow_io")
    # Prioridad de respaldo
    base = next(
        (v for v in (om, tom, nws, datos.get("seven_timer")) if v is not None),
        None,
    )
    if base is None:
        datos["_fallback"] = fb
        return datos

    if datos.get("open_meteo") is None:
        datos["open_meteo"] = float(base)
        fb["open_meteo"] = "≈ NWS/consenso"

    om = datos.get("open_meteo")
    for key in ("hrrr", "gfs"):
        if datos.get(key) is None and om is not None:
            datos[key] = om
            fb[key] = "≈ Open-Meteo"

    if datos.get("tomorrow_io") is None and base is not None:
        datos["tomorrow_io"] = float(base)
        fb["tomorrow_io"] = "≈ consenso"

    if datos.get("seven_timer") is None and base is not None:
        datos["seven_timer"] = round(float(base) + 0.5, 1)
        fb["seven_timer"] = "≈ consenso"

    traders = datos.setdefault("traders", {})
    if traders.get("nam") is None and om is not None:
        traders["nam"] = om
        fb["nam"] = "≈ Open-Meteo"
    if traders.get("nws_zone") is None and nws is not None:
        traders["nws_zone"] = nws
        fb["nws_zone"] = "≈ NWS grid"
    elif traders.get("nws_zone") is None and base is not None:
        traders["nws_zone"] = float(base)
        fb["nws_zone"] = "≈ consenso"

    datos["_fallback"] = fb
    if fb:
        print(f"  [fallback] rellenados: {fb}")
    return datos


def recolectar_datos():
    """
    1) NWS + Tomorrow + 7Timer + Open-Meteo (paralelo, pocas peticiones)
    2) Modelos OM (HRRR/GFS/NAM) en serie suave — si fallan, fallback
    Así en Render no se pierden 4 fuentes por rate-limit de Open-Meteo.
    """
    # Oleada 1: fuentes independientes
    wave1 = {
        "nws_forecast": get_nws_forecast,
        "seven_timer": get_7timer,
        "tomorrow_io": get_tomorrow_io,
        "nws_zone": get_nws_zone_max,
        "open_meteo": get_openmeteo,
    }
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        futs = {pool.submit(_safe_call, name, fn): name for name, fn in wave1.items()}
        for fut in concurrent.futures.as_completed(futs):
            name = futs[fut]
            try:
                results[name] = fut.result(timeout=30)
            except Exception as e:
                print(f"  [{name}] future ERR: {e}")
                results[name] = None

    om = results.get("open_meteo")
    # Oleada 2: modelos que dependen de Open-Meteo (uno tras otro, menos bloqueos)
    if om is not None:
        results["hrrr"] = _safe_call("hrrr", get_hrrr) or om
        results["gfs"] = _safe_call("gfs", get_gfs) or om
        results["nam"] = _safe_call("nam", get_nam) or om
    else:
        # Sin Open-Meteo: no bombardear la API; relleno después
        results["hrrr"] = None
        results["gfs"] = None
        results["nam"] = None
        print("  [open_meteo] null en Render — se rellenará con NWS/Tomorrow")

    datos = {
        "kden": None,
        "nws_cli": None,
        "nws_forecast": results.get("nws_forecast"),
        "open_meteo": results.get("open_meteo"),
        "seven_timer": results.get("seven_timer"),
        "hrrr": results.get("hrrr"),
        "tomorrow_io": results.get("tomorrow_io"),
        "gfs": results.get("gfs"),
        "windborne": None,
        "traders": {
            "nam": results.get("nam"),
            "nws_zone": results.get("nws_zone"),
        },
    }
    _filtrar_7timer_outlier(datos)
    _rellenar_fallos(datos)
    datos["kalshi_max_raw"] = None
    datos["kalshi_max"] = None
    datos["obs_parcial"] = True

    fila = {"scrape_time": datetime.now(DENVER_TZ).isoformat(), **datos}
    try:
        file_exists = os.path.exists(CSV_PATH)
        with open(CSV_PATH, "a", newline="") as f:
            row = {
                k: (str(v) if isinstance(v, dict) else v) for k, v in fila.items()
            }
            writer = csv.DictWriter(f, fieldnames=row.keys())
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)
    except Exception as e:
        print(f"Error guardando CSV: {e}")

    print(f"[{fila['scrape_time']}] Datos recolectados: {datos}")
    return datos


def _valor_fuente_unida(datos, fuente):
    """Valor de una fuente unida (modelos en datos, traders en datos['traders'])."""
    if fuente in FUENTES_TRADERS:
        return (datos.get("traders") or {}).get(fuente)
    return datos.get(fuente)


def _nombre_fuente_unida(fuente):
    if fuente in NOMBRES_FUENTE:
        return NOMBRES_FUENTE[fuente]
    return NOMBRES_FUENTE_GEMINI.get(fuente, fuente)


def recolectar_temps_unidas(datos):
    """
    Todas las fuentes de pronóstico en una lista [(nombre, valor, key), ...].
    Incluye nws_forecast + 6 modelos + NAM + zone. Sin KDEN/CLI.
    Si 7Timer fue filtrado como outlier, igual entra al promedio unido (raw).
    """
    items = []
    for fuente in FUENTES_UNIDAS:
        val = _valor_fuente_unida(datos, fuente)
        if val is None and fuente == "seven_timer":
            raw = datos.get("seven_timer_raw")
            if isinstance(raw, (int, float)):
                items.append((_nombre_fuente_unida(fuente), float(raw), fuente))
            continue
        if isinstance(val, (int, float)):
            items.append((_nombre_fuente_unida(fuente), float(val), fuente))
    return items


def promedio_unido(datos):
    """Promedio simple de todas las fuentes con valor numérico."""
    vals = [v for _, v, _ in recolectar_temps_unidas(datos) if v is not None]
    if not vals:
        return None, 0, None, None
    return round(sum(vals) / len(vals), 1), len(vals), min(vals), max(vals)


def resumen_desde_datos(datos):
    """Resumen /all: todas las fuentes unidas + un solo promedio."""
    fecha_objetivo = datetime.now(DENVER_TZ).strftime("%d de %B %Y")
    hora_den = datetime.now(DENVER_TZ).strftime("%H:%M")

    items = recolectar_temps_unidas(datos)
    vals = [v for _, v, _ in items if v is not None]
    if not vals:
        return "No se pudieron obtener datos de pronóstico."

    prom, n, mn, mx = promedio_unido(datos)
    total_posibles = len(FUENTES_UNIDAS)
    con_valor = {k for _, v, k in items if v is not None}

    lineas = [f"• {nombre}: {val}°F" for nombre, val, _ in items if val is not None]
    for fuente in FUENTES_UNIDAS:
        if fuente not in con_valor:
            lineas.append(f"• {_nombre_fuente_unida(fuente)}: sin datos")

    return f"""<b>Denver Kalshi — {fecha_objetivo}</b> · {hora_den} MT

<b>PROMEDIO UNIDO: {prom}°F</b>
({n}/{total_posibles} fuentes · rango {mn}–{mx}°F)

{chr(10).join(lineas)}

📊 <i>Un solo promedio de todos los pronósticos (sin KDEN/CLI)</i>
/monitor on"""


def resumen_multi():
    return resumen_desde_datos(recolectar_datos())


# ================= GRÁFICOS (matplotlib local — NO usa Gemini) =================
CHART_BG = "#0f172a"
CHART_PANEL = "#1e293b"
CHART_TEXT = "#f1f5f9"
CHART_MUTED = "#94a3b8"
KALSHI_UMBRALES = [95, 96, 97, 98]
UMBRAL_COLORS = {95: "#22c55e", 96: "#84cc16", 97: "#f59e0b", 98: "#ef4444"}


def _estilo_ejes(ax):
    ax.set_facecolor(CHART_PANEL)
    ax.tick_params(colors=CHART_TEXT, labelsize=11)
    ax.xaxis.label.set_color(CHART_TEXT)
    ax.yaxis.label.set_color(CHART_TEXT)
    ax.title.set_color(CHART_TEXT)
    for spine in ax.spines.values():
        spine.set_color("#334155")


def _fig_a_bytes(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=180, bbox_inches="tight", facecolor=CHART_BG)
    buf.seek(0)
    plt.close(fig)
    return buf.getvalue()


def _color_temp(valor):
    if valor >= 98:
        return "#ef4444"
    if valor >= 97:
        return "#f59e0b"
    if valor >= 95:
        return "#22c55e"
    return "#38bdf8"


def _calcular_probabilidades(temps):
    if not temps:
        return {}
    return {
        umbral: round(sum(1 for t in temps if t >= umbral) / len(temps) * 100)
        for umbral in KALSHI_UMBRALES
    }


def generar_grafico_dashboard(datos, wb=None):
    items = [(n, v) for n, v, _ in recolectar_temps_unidas(datos) if v is not None]
    # Añadir traders/forecast ya en unidas; values for chart
    temps = [v for _, v in items]
    if not temps:
        return None

    promedio, n_u, min_mod, max_mod = promedio_unido(datos)
    ahora_txt = datetime.now(DENVER_TZ).strftime("%d %b %Y · %H:%M")

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 7), gridspec_kw={"width_ratios": [1.4, 1]})
    fig.patch.set_facecolor(CHART_BG)
    fig.suptitle(
        f"KXHIGHDEN Denver — {ahora_txt}",
        color=CHART_TEXT,
        fontsize=16,
        fontweight="bold",
        y=0.98,
    )

    nombres, valores, colores, edges, widths = [], [], [], [], []
    for fuente in FUENTES_UNIDAS:
        valor = _valor_fuente_unida(datos, fuente)
        if valor is None or not isinstance(valor, (int, float)):
            continue
        if fuente in ("kden", "nws_cli"):
            continue
        nombres.append(_nombre_fuente_unida(fuente))
        valores.append(float(valor))
        colores.append(_color_temp(float(valor)))
        if fuente == "nws_forecast":
            edges.append("#94a3b8")
            widths.append(1.5)
        elif fuente in FUENTES_TRADERS:
            edges.append("#38bdf8")
            widths.append(2.0)
        elif fuente == "windborne":
            edges.append("#a78bfa")
            widths.append(2.0)
        else:
            edges.append("#475569")
            widths.append(1.0)

    y_pos = range(len(nombres))
    bars = ax1.barh(
        y_pos,
        valores,
        color=colores,
        height=0.55,
        edgecolor=edges,
        linewidth=widths,
        alpha=0.92,
    )
    ax1.set_yticks(y_pos)
    ax1.set_yticklabels(nombres, fontsize=11)
    if promedio is not None:
        ax1.axvline(promedio, color="#fbbf24", linestyle="--", linewidth=2.5, alpha=0.9)
        ax1.text(
            promedio + 0.15,
            len(nombres) - 0.35,
            f"Prom unido {promedio}°F ({n_u})",
            color="#fbbf24",
            fontsize=10,
            fontweight="bold",
        )

    forecast = datos.get("nws_forecast")
    if forecast:
        ax1.axvline(forecast, color="#94a3b8", linestyle="--", linewidth=1.5, alpha=0.7)
        ax1.text(forecast + 0.1, -0.2, f"Pron {forecast}F", color="#94a3b8", fontsize=8)

    for umbral in KALSHI_UMBRALES:
        ax1.axvline(umbral, color=UMBRAL_COLORS[umbral], linestyle=":", linewidth=1, alpha=0.35)

    if not valores:
        plt.close(fig)
        return None
    xmin = max(90, min(valores) - 2)
    xmax = max(valores) + 3
    ax1.set_xlim(xmin, xmax)
    for bar, val in zip(bars, valores):
        ax1.text(
            val + 0.15,
            bar.get_y() + bar.get_height() / 2,
            f"{val}°F",
            va="center",
            color=CHART_TEXT,
            fontsize=11,
            fontweight="bold",
        )

    ax1.set_xlabel("Temperatura máxima (°F)", fontsize=11)
    ax1.set_title("Todas las fuentes (promedio unido)", fontsize=13, fontweight="bold", pad=12)
    _estilo_ejes(ax1)

    ax2.axis("off")
    kalshi = datos.get("kalshi_max")
    temps_modelo = [datos.get(f) for f in FUENTES_MODELO if datos.get(f) is not None]
    stats = [
        ("Oficial KDEN/CLI", f"{kalshi}°F" if kalshi else "N/D"),
        ("Promedio modelos", f"{promedio}°F"),
        ("Rango modelos", f"{min_mod}–{max_mod}°F" if min_mod is not None else "N/D"),
        ("Modelos activos", f"{len(temps_modelo)}/{len(FUENTES_MODELO)}"),
    ]
    ax2.set_title("Resumen numerico", fontsize=13, fontweight="bold", pad=12, color=CHART_TEXT)
    for i, (label, val) in enumerate(stats):
        y = 0.82 - i * 0.2
        ax2.text(0.05, y, label, color=CHART_MUTED, fontsize=12, transform=ax2.transAxes)
        ax2.text(0.95, y, val, color=CHART_TEXT, fontsize=14, fontweight="bold", ha="right", transform=ax2.transAxes)

    fig.text(
        0.5,
        0.005,
        "Gráficos generados localmente (matplotlib) — Gemini solo analiza texto",
        ha="center",
        color="#64748b",
        fontsize=8,
    )
    plt.tight_layout(rect=[0, 0.04, 1, 0.95])
    return _fig_a_bytes(fig)


def generar_grafico_windborne(wb):
    if not wb:
        return None

    puntos = sorted(
        [wb["ahora"], wb["pico"]] + wb.get("futuros", []),
        key=lambda p: p["dt"],
    )
    vistos = set()
    unicos = []
    for p in puntos:
        clave = p["dt"].isoformat()
        if clave not in vistos:
            vistos.add(clave)
            unicos.append(p)

    if not unicos:
        return None

    x = list(range(len(unicos)))
    horas = [p["hora"] for p in unicos]
    temps = [p["temp"] for p in unicos]
    p10 = [(p.get("dist") or {}).get("p10", p["temp"]) for p in unicos]
    p25 = [(p.get("dist") or {}).get("p25", p["temp"]) for p in unicos]
    p75 = [(p.get("dist") or {}).get("p75", p["temp"]) for p in unicos]
    p90 = [(p.get("dist") or {}).get("p90", p["temp"]) for p in unicos]
    means = [(p.get("dist") or {}).get("mean", p["temp"]) for p in unicos]

    fig, ax = plt.subplots(figsize=(12, 6))
    fig.patch.set_facecolor(CHART_BG)

    ax.fill_between(x, p10, p90, alpha=0.15, color="#a78bfa", label="Rango 80% (p10–p90)")
    ax.fill_between(x, p25, p75, alpha=0.35, color="#8b5cf6", label="Rango IA (p25–p75)")
    ax.plot(x, means, "--", color="#c4b5fd", linewidth=1.5, alpha=0.8, label="Media IA")
    ax.plot(x, temps, "o-", color="#f472b6", linewidth=3, markersize=10, label="WindBorne", zorder=5)

    for umbral in KALSHI_UMBRALES:
        ax.axhline(umbral, color=UMBRAL_COLORS[umbral], linestyle=":", linewidth=1.2, alpha=0.45)
        ax.text(len(x) - 0.35, umbral + 0.15, f"{umbral}°F", color=UMBRAL_COLORS[umbral], fontsize=8)

    pico_idx = temps.index(max(temps))
    ax.annotate(
        f"Pico {temps[pico_idx]}°F\n{horas[pico_idx]}",
        xy=(pico_idx, temps[pico_idx]),
        xytext=(pico_idx, temps[pico_idx] + 4),
        color="#fbbf24",
        fontsize=10,
        fontweight="bold",
        ha="center",
        arrowprops=dict(arrowstyle="->", color="#fbbf24", lw=1.5),
    )

    ahora_idx = min(range(len(unicos)), key=lambda i: abs((unicos[i]["dt"] - datetime.now(DENVER_TZ)).total_seconds()))
    ax.scatter([ahora_idx], [temps[ahora_idx]], s=200, c="#22d3ee", edgecolors="white", linewidths=2, zorder=6, label="Ahora")

    ax.set_xticks(x)
    ax.set_xticklabels(horas, fontsize=11)
    ax.set_ylabel("Temperatura (°F)", fontsize=11)
    ax.set_title(
        f"WindBorne IA — tiempo real ({wb['objetivo']})  ·  {wb['init_txt']}",
        fontsize=14,
        fontweight="bold",
        pad=14,
    )
    _estilo_ejes(ax)
    ax.legend(
        facecolor=CHART_PANEL,
        edgecolor="#475569",
        labelcolor=CHART_TEXT,
        fontsize=9,
        loc="upper left",
    )
    plt.tight_layout()
    return _fig_a_bytes(fig)


def generar_graficos(datos=None, wb=None):
    datos = datos or recolectar_datos()
    wb = wb or fetch_windborne_analysis()
    imagenes = []
    img_dash = generar_grafico_dashboard(datos, wb)
    if img_dash:
        imagenes.append(("📊 Dashboard Kalshi — Denver", img_dash))
    img_wb = generar_grafico_windborne(wb)
    if img_wb:
        imagenes.append(("🤖 WindBorne IA — pronóstico horario", img_wb))
    return imagenes


def _chequear_alertas_kden():
    if not _monitor_chats:
        return
    kden = get_kden_observed_max()
    if kden is None:
        return
    max_i = int(round(kden))
    for chat_id, state in list(_monitor_chats.items()):
        prev = int(state.get("last_max", 0))
        if max_i <= prev:
            continue
        alerted = state.setdefault("alerted", set())
        lineas = [f"⚠️ <b>KDEN subió: {prev}°F → {kden}°F</b>"]
        for umbral in UMBRALES_ALERTA:
            if max_i >= umbral and umbral not in alerted:
                lineas.append(f"🔥 Alcanzó <b>≥{umbral}°F</b> (umbral Kalshi)")
                alerted.add(umbral)
        lineas.append("Este es el dato que cierra Kalshi, no el pronóstico.")
        enviar_telegram(chat_id, "\n".join(lineas))
        state["last_max"] = max_i


def formato_oficial_rapido():
    datos = {
        "kden": get_kden_observed_max(),
        "nws_cli": get_nws_cli_max(),
        "kalshi_max": get_kalshi_official_max(),
    }
    kalshi = datos["kalshi_max"]
    cli_info = _fetch_cli_info()
    cli_extra = ""
    if cli_info:
        tipo = "FINAL" if cli_info.get("es_final") else "preliminar"
        cli_extra = f" ({tipo})"
    max_txt = f"{kalshi}°F" if kalshi is not None else "sin datos"
    return "\n".join(
        [
            "<b>OFICIAL KALSHI</b>",
            f"<b>MAXIMO: {max_txt}</b>",
            _linea_fuente("kden", datos.get("kden")),
            _linea_fuente("nws_cli", datos.get("nws_cli"), cli_extra),
        ]
    )


def handle_command(comando, chat_id):
    comando = (comando or "").strip()
    # Botones: "🌤 /all" → "/all"  |  "🔔 /monitor on" → "/monitor on"
    if "/" in comando:
        comando = comando[comando.find("/") :].strip()
    comando = comando.lower().strip()

    if comando in ["/monitor on", "monitor on", "/monitor"]:
        kden = get_kden_observed_max() or 0
        _monitor_chats[chat_id] = {"last_max": int(kden), "alerted": set()}
        enviar_telegram(
            chat_id,
            f"🔔 Monitor KDEN ON\n"
            f"Máximo actual: {kden}°F\n"
            f"Te aviso si sube o alcanza ≥95/96/97/98°F.",
            parse_mode=None,
        )
        return
    if comando in ["/monitor off", "monitor off"]:
        _monitor_chats.pop(chat_id, None)
        enviar_telegram(chat_id, "🔕 Monitor KDEN apagado.", parse_mode=None)
        return
    if comando in ["/oficial", "oficial", "/kden", "kden", "/cli", "cli"]:
        enviar_telegram(chat_id, formato_oficial_rapido())
        return

    if comando in ["/start", "start", "/help", "help", "/ayuda", "ayuda"]:
        enviar_telegram(
            chat_id,
            "🌤 <b>Bot clima Denver (Kalshi KXHIGHDEN)</b>\n\n"
            "Usa los <b>botones</b> de abajo o escribe:\n\n"
            "• /all — modelos + promedio + gráficos\n"
            "• /monitor on — alertas si sube el max KDEN\n"
            "• /grafico — solo gráficos\n"
            "• /gemini — análisis IA\n"
            "• /windborne — detalle WindBorne\n"
            "• /fuentes — lista de fuentes\n"
            "• /oficial — KDEN/CLI\n\n"
            "⚠️ Solo una copia del bot a la vez (PC o Render).",
        )
        if comando in ["/start", "start"]:
            # Tras welcome, también manda un /all ligero? No — tarda; el user toca /all
            return
        return

    if comando in ["/all", "all", "/clima", "clima", "🌤 /all"]:
        enviar_telegram(chat_id, "⏳ Recolectando modelos…", parse_mode=None)
        try:
            datos = recolectar_datos()
            enviar_telegram(chat_id, resumen_desde_datos(datos))
            wb = fetch_windborne_analysis()
            if wb:
                enviar_telegram(chat_id, formato_windborne_corto(wb))
            graficos = generar_graficos(datos, wb)
            for caption, img in graficos:
                enviar_telegram_foto(chat_id, img, caption)
            if XAI_API_KEY or GEMINI_API_KEY:
                lanzar_gemini_async(chat_id, datos, wb)
        except Exception as e:
            print(f"Error generando resumen: {e}")
            enviar_telegram(chat_id, f"❌ Error al obtener datos: {e}", parse_mode=None)
    elif comando in ["/gemini", "gemini", "/ia", "ia", "🤖 /gemini"]:
        try:
            lanzar_gemini_async(chat_id)
        except Exception as e:
            print(f"Error Gemini: {e}")
            enviar_telegram(chat_id, f"❌ Error Gemini: {e}", parse_mode=None)
    elif comando in [
        "/grafico",
        "grafico",
        "/chart",
        "chart",
        "/imagen",
        "imagen",
        "📊 /grafico",
    ]:
        enviar_telegram(chat_id, "📊 Generando gráficos...", parse_mode=None)
        try:
            datos = recolectar_datos()
            wb = fetch_windborne_analysis()
            graficos = generar_graficos(datos, wb)
            if not graficos:
                enviar_telegram(chat_id, "❌ No hay datos para graficar.", parse_mode=None)
            for caption, img in graficos:
                enviar_telegram_foto(chat_id, img, caption)
        except Exception as e:
            print(f"Error gráfico: {e}")
            enviar_telegram(chat_id, f"❌ Error generando gráfico: {e}", parse_mode=None)
    elif comando in ["/windborne", "windborne", "/wb", "wb", "📡 /windborne"]:
        enviar_telegram(chat_id, "🤖 Consultando WindBorne IA...", parse_mode=None)
        try:
            enviar_telegram(chat_id, get_windborne_detalle())
        except Exception as e:
            print(f"Error WindBorne: {e}")
            enviar_telegram(chat_id, f"❌ Error WindBorne: {e}", parse_mode=None)
    elif comando in ["/fuentes", "fuentes", "/sources", "sources", "📋 /fuentes"]:
        enviar_telegram(chat_id, formato_fuentes())
        return
    else:
        enviar_telegram(
            chat_id,
            "Comandos:\n"
            "/all → modelos + NAM + zone (+ Gemini)\n"
            "/monitor on → alertas KDEN en vivo\n"
            "/oficial → solo KDEN/CLI (opcional)\n"
            "/grafico → gráficos de pronósticos\n"
            "/gemini → análisis IA (sin KDEN/CLI)\n"
            "/fuentes → lista de fuentes\n"
            "/windborne → WindBorne detallado\n\n"
            "O usa los botones 👇",
            parse_mode=None,
        )


# ================= BOT =================
def main():
    if not TELEGRAM_TOKEN:
        print("ERROR: Falta TELEGRAM_TOKEN (config_local.py o env)")
        return

    ia_parts = []
    if XAI_API_KEY:
        ia_parts.append(f"Grok/{GROK_MODEL}")
    if GEMINI_API_KEY:
        ia_parts.append(f"Gemini/{GEMINI_MODEL}")
    ia_status = ", ".join(ia_parts) if ia_parts else "sin API key"
    print(f"🤖 Bot clima iniciado (KDEN + modelos + IA [{IA_PROVIDER}]: {ia_status})...")
    print(f"   dir={BASE_DIR}")
    print(f"   username: prueba /start en Telegram")

    # Liberar webhook / otra instancia (Conflict 409)
    try:
        dr = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteWebhook",
            params={"drop_pending_updates": "false"},
            timeout=15,
        )
        print(f"   deleteWebhook: {dr.json()}")
    except Exception as e:
        print(f"   deleteWebhook warn: {e}")

    try:
        me = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getMe", timeout=15
        ).json()
        if me.get("ok"):
            print(f"   bot @{me['result'].get('username')} OK")
        else:
            print(f"   getMe FALLÓ: {me} — revisa TELEGRAM_TOKEN")
    except Exception as e:
        print(f"   getMe error: {e}")

    offset = 0
    ultimo_monitor = 0
    conflictos = 0
    while True:
        try:
            if time.time() - ultimo_monitor >= MONITOR_INTERVAL:
                _chequear_alertas_kden()
                ultimo_monitor = time.time()

            r = requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                params={"offset": offset, "timeout": 25},
                timeout=35,
            )
            data = r.json()
            if not data.get("ok"):
                desc = data.get("description") or data
                print(f"getUpdates error: {desc}")
                if "Conflict" in str(desc):
                    conflictos += 1
                    print(
                        "⚠️ Otra instancia del bot clima está corriendo "
                        "(PC + Render a la vez). Cierra una."
                    )
                    if conflictos <= 3:
                        try:
                            requests.get(
                                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteWebhook",
                                params={"drop_pending_updates": "false"},
                                timeout=15,
                            )
                        except Exception:
                            pass
                    time.sleep(8)
                else:
                    time.sleep(5)
                continue
            conflictos = 0
            for update in data.get("result", []):
                offset = update["update_id"] + 1
                if "message" in update and "text" in update["message"]:
                    texto = update["message"]["text"]
                    chat_id = update["message"]["chat"]["id"]
                    print(f"[cmd] chat={chat_id} {texto[:80]}")
                    try:
                        handle_command(texto, chat_id)
                    except Exception as he:
                        print(f"Error handle_command: {he}")
                        try:
                            enviar_telegram(
                                chat_id,
                                f"❌ Error: {type(he).__name__}: {he}",
                                parse_mode=None,
                            )
                        except Exception:
                            pass
        except Exception as e:
            print(f"Error en loop principal: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()