import json
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from cities import DEFAULT_CITY_ID, KALSHI_CITIES, get_city
from config import (
    API_RETRY_DELAYS,
    BATCH_CACHE_TTL,
    CACHE_DIR,
    CYCLONE_CACHE_TTL,
    DISK_CACHE_TTL,
    MIN_INTERVAL,
    MODELO_PRINCIPAL,
    STALE_CACHE_TTL,
    VAR_CACHE_TTL,
    WIND_KEY,
)
from utils import es_error_cuota, es_error_rate_limit

_var_cache = {}
_batch_cache = {"ts": 0, "data": None, "city_ids": []}
_api_lock = threading.Lock()
_last_call = 0.0
_rate_limited_until = 0.0
_api_stats = {
    "ok": 0,
    "fail": 0,
    "quota": 0,
    "cache_hit": 0,
    "disk_hit": 0,
    "last_error": "",
    "last_ok_ts": 0.0,
    "quota_exhausted": False,
}
_DISK_BATCH = Path(CACHE_DIR) / "batch_wm6.json"


class WindBorneError(Exception):
    """Error base WindBorne API."""


class WindBorneAuthError(WindBorneError):
    """Key inválida o sin permisos (401/403)."""


class WindBorneRateLimitError(WindBorneError):
    """Demasiadas peticiones (429)."""


def api_status() -> dict:
    """Estado para /status."""
    key = WIND_KEY or ""
    return {
        "key_ok": bool(key) and key.startswith("wb_"),
        "key_tail": key[-8:] if len(key) >= 8 else "?",
        "quota_exhausted": _api_stats["quota_exhausted"],
        "ok": _api_stats["ok"],
        "fail": _api_stats["fail"],
        "quota_hits": _api_stats["quota"],
        "cache_hit": _api_stats["cache_hit"],
        "disk_hit": _api_stats["disk_hit"],
        "last_error": _api_stats["last_error"][:200],
        "batch_age": batch_cache_age(),
        "last_ok_ago": (
            int(time.time() - _api_stats["last_ok_ts"])
            if _api_stats["last_ok_ts"]
            else None
        ),
    }


def _throttle():
    global _last_call, _rate_limited_until
    now = time.time()
    if now < _rate_limited_until:
        time.sleep(_rate_limited_until - now)
    elapsed = time.time() - _last_call
    if elapsed < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - elapsed)
    _last_call = time.time()


def _cache_get(key, max_age):
    cached = _var_cache.get(key)
    if cached and time.time() - cached["ts"] < max_age:
        _api_stats["cache_hit"] += 1
        return cached["data"]
    return None


def _cache_put(key, data):
    _var_cache[key] = {"data": data, "ts": time.time()}


def _load_disk_batch():
    if not _DISK_BATCH.exists():
        return None
    try:
        obj = json.loads(_DISK_BATCH.read_text(encoding="utf-8"))
        age = time.time() - float(obj.get("ts") or 0)
        if age > DISK_CACHE_TTL:
            return None
        _api_stats["disk_hit"] += 1
        return obj
    except Exception:
        return None


def _save_disk_batch(data, city_ids):
    try:
        payload = {
            "ts": time.time(),
            "city_ids": list(city_ids),
            "data": data,
        }
        _DISK_BATCH.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as e:
        print(f"  [cache] disk save fail: {e}")


def _friendly_http_error(status, body=""):
    body_l = (body or "").lower()
    if status in (401, 403):
        return (
            "Key WindBorne inválida o expirada (HTTP "
            f"{status}). Revisa WINDBORNE_API_KEY en config."
        )
    if status == 429:
        # Free trial agotado (2000 req totales) vs rate limit temporal
        if es_error_cuota(body_l):
            return (
                "⛔ CUOTA WindBorne AGOTADA (free trial 2000/2000).\n"
                "No se arregla esperando: hay que subir de plan en "
                "WindBorne o poner una key nueva con cuota.\n"
                "Config: WINDBORNE_API_KEY en windborne_monitor/config.py"
            )
        return (
            "Límite temporal de peticiones WindBorne (429). "
            "Espera 1–2 min y reintenta."
        )
    if status == 404:
        return f"Variable no disponible en este modelo (HTTP 404)."
    return f"Error WindBorne HTTP {status}"


def _wb_get(url, params, cache_key=None):
    """GET con lock global, reintentos 429 y caché stale."""
    global _rate_limited_until

    # Si ya sabemos que la trial está muerta, no gastar más llamadas
    if _api_stats["quota_exhausted"] and cache_key:
        stale = _cache_get(cache_key, STALE_CACHE_TTL)
        if stale is not None:
            return stale

    if cache_key:
        fresh = _cache_get(cache_key, VAR_CACHE_TTL)
        if fresh is not None:
            return fresh

    headers = {"Authorization": f"Bearer {WIND_KEY}"}
    last_err = ""

    with _api_lock:
        # Doble-check tras adquirir lock
        if cache_key:
            fresh = _cache_get(cache_key, VAR_CACHE_TTL)
            if fresh is not None:
                return fresh

        for attempt, delay in enumerate([0] + list(API_RETRY_DELAYS)):
            if _api_stats["quota_exhausted"] and attempt > 0:
                break
            if delay:
                _rate_limited_until = max(_rate_limited_until, time.time() + delay)
                time.sleep(delay)
            _throttle()
            try:
                r = requests.get(url, headers=headers, params=params, timeout=25)
            except requests.RequestException as e:
                last_err = str(e)
                _api_stats["fail"] += 1
                _api_stats["last_error"] = last_err
                continue

            if r.status_code == 200:
                data = r.json()
                if cache_key:
                    _cache_put(cache_key, data)
                _api_stats["ok"] += 1
                _api_stats["last_ok_ts"] = time.time()
                _api_stats["quota_exhausted"] = False
                return data

            last_err = _friendly_http_error(r.status_code, r.text)
            _api_stats["fail"] += 1
            _api_stats["last_error"] = last_err
            if r.status_code == 429:
                body_l = (r.text or "").lower()
                # Cuota free trial agotada: no reintentar (no se recupera sola)
                if es_error_cuota(body_l):
                    _api_stats["quota"] += 1
                    _api_stats["quota_exhausted"] = True
                    raise WindBorneRateLimitError(last_err)
                _rate_limited_until = time.time() + delay if delay else time.time() + 8
                continue
            if r.status_code in (401, 403):
                raise WindBorneAuthError(last_err)
            if attempt >= len(API_RETRY_DELAYS):
                break

    if cache_key:
        stale = _cache_get(cache_key, STALE_CACHE_TTL)
        if stale is not None:
            return stale

    if es_error_rate_limit(last_err):
        raise WindBorneRateLimitError(last_err)
    raise WindBorneError(last_err or "Sin respuesta WindBorne")


def _time_range(city):
    tz = city["tz"]
    inicio = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    fin = inicio + timedelta(days=1) - timedelta(seconds=1)
    utc = ZoneInfo("UTC")
    return (
        inicio.astimezone(utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        fin.astimezone(utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def _batch_time_range(cities):
    utc = ZoneInfo("UTC")
    inicios, fines = [], []
    for city in cities:
        tz = city["tz"]
        inicio = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
        fin = inicio + timedelta(days=1) - timedelta(seconds=1)
        inicios.append(inicio.astimezone(utc))
        fines.append(fin.astimezone(utc))
    return (
        min(inicios).strftime("%Y-%m-%dT%H:%M:%SZ"),
        max(fines).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def batch_cache_age():
    if not _batch_cache["data"]:
        return None
    return int(time.time() - _batch_cache["ts"])


def batch_cache_fresh():
    age = batch_cache_age()
    return age is not None and age < BATCH_CACHE_TTL


def fetch_all_cities_forecast(force=False):
    if not force and batch_cache_fresh():
        return _batch_cache["data"]

    # Disco: sobrevive reinicios y salva cuota free trial
    if not force:
        disk = _load_disk_batch()
        if disk and disk.get("data"):
            _batch_cache["ts"] = float(disk["ts"])
            _batch_cache["data"] = disk["data"]
            _batch_cache["city_ids"] = disk.get("city_ids") or list(KALSHI_CITIES.keys())
            if batch_cache_fresh() or _api_stats["quota_exhausted"]:
                return _batch_cache["data"]

    cities = [get_city(cid) for cid in KALSHI_CITIES]
    coords = ";".join(f"{c['lat']},{c['lon']}" for c in cities)
    min_t, max_t = _batch_time_range(cities)

    url = f"https://api.windbornesystems.com/forecasts/v1/{MODELO_PRINCIPAL}/point_forecast/interpolated"
    params = {
        "coordinates": coords,
        "variable": "temperature_2m",
        "min_forecast_time": min_t,
        "max_forecast_time": max_t,
        "include_distribution": "true",
    }
    try:
        data = _wb_get(url, params)
    except WindBorneError:
        # Último recurso: disco aunque esté "viejo"
        disk = _load_disk_batch()
        if disk and disk.get("data"):
            _batch_cache["ts"] = float(disk["ts"])
            _batch_cache["data"] = disk["data"]
            _batch_cache["city_ids"] = disk.get("city_ids") or list(KALSHI_CITIES.keys())
            return _batch_cache["data"]
        raise

    city_ids = list(KALSHI_CITIES.keys())
    _batch_cache["ts"] = time.time()
    _batch_cache["data"] = data
    _batch_cache["city_ids"] = city_ids
    _save_disk_batch(data, city_ids)
    return data


def city_raw_from_batch(city_id, batch=None):
    batch = batch or fetch_all_cities_forecast()
    city_ids = _batch_cache["city_ids"] or list(KALSHI_CITIES.keys())
    idx = city_ids.index(city_id)
    forecasts = batch.get("forecasts", [])
    if idx >= len(forecasts):
        raise IndexError(f"Sin datos batch para {city_id}")
    return {
        "forecasts": [forecasts[idx]],
        "initialization_time": batch.get("initialization_time", ""),
    }


def fetch_variable(variable, city=None, model=MODELO_PRINCIPAL, with_dist=True, force=False):
    city = city or get_city(DEFAULT_CITY_ID)
    cache_key = f"{city['id']}:{model}:{variable}:{with_dist}"

    if not force:
        fresh = _cache_get(cache_key, VAR_CACHE_TTL)
        if fresh is not None:
            return fresh

    min_t, max_t = _time_range(city)
    url = f"https://api.windbornesystems.com/forecasts/v1/{model}/point_forecast/interpolated"
    params = {
        "coordinates": f"{city['lat']},{city['lon']}",
        "variable": variable,
        "min_forecast_time": min_t,
        "max_forecast_time": max_t,
    }
    if with_dist and model == MODELO_PRINCIPAL:
        params["include_distribution"] = "true"

    return _wb_get(url, params, cache_key=cache_key)


def fetch_variables(variables, city=None, model=MODELO_PRINCIPAL, with_dist=True):
    """Varias variables en serie (una petición cada MIN_INTERVAL)."""
    out = {}
    errors = {}
    for var in variables:
        try:
            out[var] = fetch_variable(var, city=city, model=model, with_dist=with_dist)
        except WindBorneError as e:
            errors[var] = str(e)
    return out, errors


def fetch_tropical_cyclones(basin, model=MODELO_PRINCIPAL, force=False):
    """Ciclones tropicales activos (pronóstico WindBorne) para una cuenca.

    Basins válidos (confirmados por la API): AL, EP, CP, WP, NI, SI, AU, SP.
    """
    cache_key = f"cyclones:{model}:{basin}"
    if not force:
        fresh = _cache_get(cache_key, CYCLONE_CACHE_TTL)
        if fresh is not None:
            return fresh

    url = f"https://api.windbornesystems.com/forecasts/v1/{model}/tropical_cyclones"
    return _wb_get(url, {"basin": basin}, cache_key=cache_key)


def fetch_forecast(city=None, force=False):
    city = city or get_city(DEFAULT_CITY_ID)
    if not force and batch_cache_fresh():
        try:
            return city_raw_from_batch(city["id"])
        except (ValueError, IndexError):
            pass
    return fetch_variable("temperature_2m", city=city, force=force)


def parse_points(raw, variable, city):
    forecasts = raw.get("forecasts", [])
    if not forecasts:
        return []

    points_raw = forecasts[0] if isinstance(forecasts[0], list) else forecasts
    objetivo = datetime.now(city["tz"]).strftime("%Y-%m-%d")
    puntos = []

    for p in points_raw:
        ts = p.get("time")
        val = p.get(variable)
        if ts is None or val is None:
            continue
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(city["tz"])
        if dt.strftime("%Y-%m-%d") != objetivo:
            continue
        puntos.append(
            {
                "dt": dt,
                "hora": dt.strftime("%H:%M"),
                "valor": val,
                "dist": p.get("distribution"),
                "raw": p,
            }
        )

    return sorted(puntos, key=lambda x: x["dt"])


def get_points_hoy(raw=None, city=None):
    city = city or get_city(DEFAULT_CITY_ID)
    raw = raw or fetch_forecast(city=city)
    puntos = parse_points(raw, "temperature_2m", city)
    for p in puntos:
        p["temp_c"] = p["valor"]
        p["temp_f"] = round(p["valor"] * 9 / 5 + 32, 1)
    return puntos