"""Tracking de ciclones tropicales via WindBorne forecasts API (WM-6).

Estructura real observada en tropical_cyclones[id] (2026-07-28, basin EP):
{
  "tropical_cyclone_id", "operational_id", "has_official_id",
  "genesis": {"latitude", "longitude"},
  "storm_name", "basin", "start_time", "end_time",
  "wind_kt", "mslp_hpa"
}
No es un array de puntos de trayectoria como sugiere la doc publica; es un registro
por tormenta. El parseo es defensivo por si aparecen campos de track/posiciones
futuras cuando la tormenta esta mas desarrollada.
"""
from api import WindBorneError, fetch_tropical_cyclones
from cities import KALSHI_CITIES
from utils import distancia_km

BASINS = {
    "AL": "Atlántico",
    "EP": "Pacífico Este",
    "CP": "Pacífico Central",
    "WP": "Pacífico Oeste",
    "NI": "Índico Norte",
    "SI": "Índico Suroeste",
    "AU": "Australia",
    "SP": "Pacífico Sur",
}

# Cuencas que pueden afectar a las ciudades Kalshi (EE.UU. continental).
BASINS_USA = ["AL", "EP", "CP"]

_TRACK_KEYS = ("track", "positions", "points", "forecast_track")


def _lat_lon(punto):
    if not punto:
        return None, None
    lat = punto.get("lat", punto.get("latitude"))
    lon = punto.get("lon", punto.get("lng", punto.get("longitude")))
    return lat, lon


def _posicion_actual(storm):
    """Usa el track si la API lo trae; si no, cae al punto de génesis."""
    for key in _TRACK_KEYS:
        track = storm.get(key)
        if isinstance(track, list) and track:
            return track[0]
    genesis = storm.get("genesis") or {}
    if genesis.get("latitude") is None or genesis.get("longitude") is None:
        return None
    return {"lat": genesis["latitude"], "lon": genesis["longitude"], "time": storm.get("start_time")}


def _ciudad_mas_cercana(lat, lon):
    if lat is None or lon is None:
        return None, None
    mejor_nombre, mejor_d = None, None
    for c in KALSHI_CITIES.values():
        d = distancia_km(lat, lon, c["lat"], c["lon"])
        if mejor_d is None or d < mejor_d:
            mejor_d, mejor_nombre = d, c["nombre"]
    return mejor_nombre, round(mejor_d) if mejor_d is not None else None


def ciclones_activos(basins=None, model="wm-6", force=False):
    """Devuelve (activos, errores). activos: {storm_id: storm_dict + '_basin'}."""
    basins = basins or BASINS_USA
    activos = {}
    errores = {}
    for b in basins:
        try:
            raw = fetch_tropical_cyclones(basin=b, model=model, force=force)
        except WindBorneError as e:
            errores[b] = str(e)
            continue
        tormentas = raw.get("tropical_cyclones") or {}
        for storm_id, storm in tormentas.items():
            storm = dict(storm)
            storm["_basin"] = b
            activos[storm_id] = storm
    return activos, errores


def msg_huracanes(global_=False, force=False):
    basins = list(BASINS.keys()) if global_ else BASINS_USA
    activos, errores = ciclones_activos(basins=basins, force=force)

    titulo = "Ciclones tropicales — Global" if global_ else "Ciclones tropicales — Cuencas USA"
    lineas = [f"<b>{titulo}</b>"]

    if not activos:
        lineas.append("Sin ciclones tropicales activos en las cuencas consultadas.")
        if not global_:
            lineas.append("<i>Prueba /huracanes global para ver todas las cuencas.</i>")
    else:
        for storm_id, storm in activos.items():
            nombre = storm.get("storm_name") or storm_id
            basin_txt = BASINS.get(storm.get("_basin"), storm.get("_basin", "?"))
            punto = _posicion_actual(storm)
            lat, lon = _lat_lon(punto)

            wind = storm.get("wind_kt")
            pres = storm.get("mslp_hpa")
            wind_txt = f"{wind} kt" if wind is not None else "sin clasificar aún"
            pres_txt = f" · {pres} hPa" if pres is not None else ""

            lineas.append(f"\n<b>{nombre}</b> ({storm_id} · {basin_txt})")
            if lat is not None and lon is not None:
                lineas.append(f"Posición: {lat:.1f}, {lon:.1f}")
                ciudad, dist_km = _ciudad_mas_cercana(lat, lon)
                if ciudad and dist_km is not None and dist_km < 2500:
                    lineas.append(f"Más cercano a: {ciudad} (~{dist_km} km)")
            lineas.append(f"Viento: {wind_txt}{pres_txt}")
            if storm.get("start_time") and storm.get("end_time"):
                lineas.append(
                    f"Ventana pronóstico: {storm['start_time'][:16]} → {storm['end_time'][:16]}"
                )

    if errores:
        nombres = [BASINS.get(b, b) for b in errores]
        lineas.append(f"\n<i>Sin datos en: {', '.join(nombres)}</i>")

    return "\n".join(lineas)
