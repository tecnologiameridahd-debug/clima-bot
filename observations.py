import time
from datetime import datetime, timezone

import requests

from cities import KALSHI_CITIES
from utils import c_to_f as _c_to_f

METAR_TTL = 120
_metar_cache = {"ts": 0, "by_station": {}}

NWS_HEADERS = {
    "User-Agent": "WindBorneMonitor (alberto@example.com)",
    "Accept": "application/geo+json",
}
MAX_HOY_TTL = 300  # 5 min — el maximo real del dia solo puede subir
_max_hoy_cache = {}


def fetch_all_metar(force=False):
    if not force and _metar_cache["by_station"] and time.time() - _metar_cache["ts"] < METAR_TTL:
        return _metar_cache["by_station"]

    ids = ",".join(c["station"] for c in KALSHI_CITIES.values())
    r = requests.get(
        "https://aviationweather.gov/api/data/metar",
        params={"ids": ids, "format": "json"},
        timeout=30,
    )
    r.raise_for_status()
    by_station = {row["icaoId"]: row for row in r.json()}
    _metar_cache["ts"] = time.time()
    _metar_cache["by_station"] = by_station
    return by_station


def _fetch_metar_station(station_id):
    station_id = station_id.upper()
    try:
        r = requests.get(
            "https://aviationweather.gov/api/data/metar",
            params={"ids": station_id, "format": "json"},
            timeout=15,
        )
        r.raise_for_status()
        items = r.json()
        if items:
            return items[0]
    except Exception as e:
        print(f"  [metar] {station_id}: {e}")
    return None


def get_metar_obs(station_id, force=False):
    if not station_id:
        return None
    data = fetch_all_metar(force=force)
    row = data.get(station_id.upper())
    if (not row or row.get("temp") is None) and station_id:
        row = _fetch_metar_station(station_id)
    if not row or row.get("temp") is None:
        return None

    report = row.get("reportTime") or row.get("receiptTime", "")
    age_min = None
    hora_local = ""
    if report:
        try:
            rt = datetime.fromisoformat(report.replace("Z", "+00:00"))
            age_min = int((datetime.now(timezone.utc) - rt).total_seconds() / 60)
            hora_local = rt.strftime("%H:%M UTC")
        except ValueError:
            pass

    return {
        "temp_f": _c_to_f(row["temp"]),
        "temp_c": row["temp"],
        "age_min": age_min,
        "hora": hora_local,
        "station": station_id.upper(),
        "raw_type": row.get("metarType", "METAR"),
    }


# Antes de esta hora local, un único obs nocturno NO es el high del día (Kalshi).
_MAX_DAYLIGHT_START = 6   # 00:00–05:59 no cuentan para el MÁX
_MAX_CONFIABLE_HOUR = 10  # antes de las 10, el max es provisional


def metar_extremos_hoy(station_id, tz, force=False, fecha=None):
    """Máximo y mínimo REALES de un día local (NWS).

    fecha: 'YYYY-MM-DD' en zona de la ciudad. Default = hoy local.
    NUNCA mezcla con otro día (si el 29 arranca, no devuelve max del 28).

    El MÁX ignora lecturas 00:00–05:59 (madrugada): si no, a la 1am sale
    "máx del día = 70°F" = temp actual, no el high diurno.
    """
    if not station_id:
        return None
    station_id = station_id.upper()
    hoy_local = fecha or datetime.now(tz).strftime("%Y-%m-%d")
    clave = f"{station_id}:{hoy_local}"

    cached = _max_hoy_cache.get(clave)
    if not force and cached and time.time() - cached["ts"] < MAX_HOY_TTL:
        return cached["data"]

    def _parse_feats(feats, dia):
        mejor = peor = None  # max diurno / min cualquier hora
        mejor_raw = None     # max incluyendo madrugada (solo diagnóstico)
        n = 0
        n_daylight = 0
        for f in feats:
            props = f.get("properties", {})
            temp_c = (props.get("temperature") or {}).get("value")
            ts = props.get("timestamp")
            if temp_c is None or not ts:
                continue
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(tz)
            except ValueError:
                continue
            if dt.strftime("%Y-%m-%d") != dia:
                continue
            n += 1
            hora_local = dt.strftime("%H:%M")
            punto = {
                "temp_c": temp_c,
                "temp_f": _c_to_f(temp_c),
                "hora": hora_local,
                "hour": dt.hour,
            }
            if peor is None or temp_c < peor["temp_c"]:
                peor = punto
            if mejor_raw is None or temp_c > mejor_raw["temp_c"]:
                mejor_raw = punto
            # High del día: solo diurnas (evita 70°F a la 1am como "máx")
            if dt.hour >= _MAX_DAYLIGHT_START:
                n_daylight += 1
                if mejor is None or temp_c > mejor["temp_c"]:
                    mejor = punto
        return mejor, peor, n, n_daylight, mejor_raw

    # Pedir desde 00:00 local del día pedido (UTC)
    try:
        y, m, d = [int(x) for x in hoy_local.split("-")]
        inicio = datetime(y, m, d, 0, 0, 0, tzinfo=tz)
        start = inicio.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        # un poco después del fin del día para no cortar obs cerca de medianoche
        from datetime import timedelta

        fin = inicio + timedelta(days=1, hours=2)
        end = fin.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        r = requests.get(
            f"https://api.weather.gov/stations/{station_id}/observations",
            params={"start": start, "end": end, "limit": 500},
            headers=NWS_HEADERS,
            timeout=10,
        )
        r.raise_for_status()
        feats = r.json().get("features", [])
    except Exception as e:
        print(f"  [metar_extremos] {station_id} {hoy_local}: {e}")
        # reintento sin end
        try:
            r = requests.get(
                f"https://api.weather.gov/stations/{station_id}/observations",
                params={"start": start, "limit": 500},
                headers=NWS_HEADERS,
                timeout=10,
            )
            r.raise_for_status()
            feats = r.json().get("features", [])
        except Exception as e2:
            print(f"  [metar_extremos] retry {station_id}: {e2}")
            return cached["data"] if cached else None

    mejor, peor, n_hoy, n_daylight, mejor_raw = _parse_feats(feats, hoy_local)

    # Si el día local acaba de empezar (0 obs), reintentar lista reciente
    # PERO filtrando solo ese día — no mezclar con el día anterior.
    if n_hoy == 0:
        try:
            r2 = requests.get(
                f"https://api.weather.gov/stations/{station_id}/observations",
                params={"limit": 200},
                headers=NWS_HEADERS,
                timeout=10,
            )
            r2.raise_for_status()
            mejor, peor, n_hoy, n_daylight, mejor_raw = _parse_feats(
                r2.json().get("features", []), hoy_local
            )
        except Exception as e:
            print(f"  [metar_extremos] list {station_id}: {e}")

    ahora_local = datetime.now(tz)
    hora_ahora = ahora_local.hour
    # Sin lecturas diurnas aún (solo madrugada): NO hay "máx del día"
    max_provisional = mejor is None or (
        hora_ahora < _MAX_CONFIABLE_HOUR and n_daylight < 3
    )

    if mejor is None and peor is None:
        # Día en curso sin observaciones todavía (p. ej. 00:05 local)
        resultado = {
            "station": station_id,
            "n_obs": 0,
            "fecha": hoy_local,
            "periodo": "dia_local",
            "max": None,
            "min": None,
            "sin_obs": True,
            "max_provisional": True,
            "obs_actual": (
                {
                    "temp_f": mejor_raw["temp_f"],
                    "hora": mejor_raw["hora"],
                }
                if mejor_raw
                else None
            ),
        }
        _max_hoy_cache[clave] = {"ts": time.time(), "data": resultado}
        return resultado

    # Si solo hay madrugada: no devolver max=70 como high del día
    max_out = None
    if mejor is not None and not (
        hora_ahora < _MAX_CONFIABLE_HOUR and n_daylight == 0
    ):
        max_out = {
            "temp_f": mejor["temp_f"],
            "hora": mejor["hora"],
            "station": station_id,
            "fecha": hoy_local,
            "provisional": bool(max_provisional),
        }
    elif mejor_raw is not None and hora_ahora < _MAX_CONFIABLE_HOUR:
        print(
            f"  [metar_extremos] {station_id}: max madrugada "
            f"{mejor_raw['temp_f']}F @{mejor_raw['hora']} — NO es high del día"
        )

    resultado = {
        "station": station_id,
        "n_obs": n_hoy,
        "n_daylight": n_daylight,
        "fecha": hoy_local,
        "periodo": "dia_local",
        "sin_obs": n_hoy == 0,
        "max": max_out,
        "max_provisional": bool(max_provisional or max_out is None),
        "obs_actual": (
            {
                "temp_f": mejor_raw["temp_f"],
                "hora": mejor_raw["hora"],
            }
            if mejor_raw
            else None
        ),
        "min": (
            {
                "temp_f": peor["temp_f"],
                "hora": peor["hora"],
                "station": station_id,
                "fecha": hoy_local,
            }
            if peor
            else None
        ),
    }
    _max_hoy_cache[clave] = {"ts": time.time(), "data": resultado}
    # Archivo diario: no se pierde al cambiar de día
    try:
        _persist_extremos_dia(station_id, hoy_local, resultado)
    except Exception as e:
        print(f"  [metar_extremos] persist: {e}")
    return resultado


def _persist_extremos_dia(station_id, fecha, resultado):
    """Guarda max/min NWS por estación+fecha (historial por día)."""
    import json
    from pathlib import Path

    from config import BASE_DIR

    path = Path(BASE_DIR) / "dias_nws_extremos.json"
    store = {}
    if path.exists():
        try:
            store = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            store = {}
    key = f"{station_id}:{fecha}"
    prev = store.get(key) or {}
    # high-water del real del día (el max solo puede subir)
    out = {
        "station": station_id,
        "fecha": fecha,
        "n_obs": resultado.get("n_obs"),
        "max": resultado.get("max") or prev.get("max"),
        "min": resultado.get("min") or prev.get("min"),
    }
    if prev.get("max") and out.get("max"):
        if float(prev["max"]["temp_f"]) > float(out["max"]["temp_f"]):
            out["max"] = prev["max"]
    if prev.get("min") and out.get("min"):
        if float(prev["min"]["temp_f"]) < float(out["min"]["temp_f"]):
            out["min"] = prev["min"]
    elif prev.get("min") and not out.get("min"):
        out["min"] = prev["min"]
    store[key] = out
    # purgar > 30 días
    from datetime import datetime, timedelta

    cutoff = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")
    for k in list(store.keys()):
        f = k.rsplit(":", 1)[-1]
        if len(f) == 10 and f < cutoff:
            del store[k]
    path.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")


def metar_extremos_fecha(station_id, tz, fecha):
    """Alias explícito: extremos NWS de un día local concreto."""
    return metar_extremos_hoy(station_id, tz, force=True, fecha=fecha)


def metar_max_hoy(station_id, tz, force=False):
    """Máximo REAL observado hoy (NWS). Compat wrapper sobre metar_extremos_hoy."""
    ext = metar_extremos_hoy(station_id, tz, force=force)
    if not ext:
        return None
    # Cache nueva forma {max,min} o legacy {temp_f,...}
    if ext.get("max"):
        m = dict(ext["max"])
        m["n_obs"] = ext.get("n_obs")
        m["station"] = ext.get("station") or m.get("station")
        return m
    if ext.get("temp_f") is not None:
        return ext
    return None


def metar_min_hoy(station_id, tz, force=False):
    """Mínimo REAL observado hoy (NWS)."""
    ext = metar_extremos_hoy(station_id, tz, force=force)
    if not ext:
        return None
    if ext.get("min"):
        m = dict(ext["min"])
        m["n_obs"] = ext.get("n_obs")
        m["station"] = ext.get("station") or m.get("station")
        return m
    return None


def metar_linea(station_id):
    obs = get_metar_obs(station_id)
    if not obs:
        return "Observación METAR: sin datos"
    edad = f"hace {obs['age_min']} min" if obs["age_min"] is not None else "reciente"
    return (
        f"<b>Temp REAL ({obs['station']})</b>: {obs['temp_f']}°F "
        f"({edad} · {obs['raw_type']})"
    )