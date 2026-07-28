import sys
import threading
import time

import requests

# Consolas no-UTF8 (cp1252 en Windows) revientan con los emojis de los logs.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from analysis import (
    analizar,
    detectar_cambio_pico,
    log_peak,
    msg_ahora,
    msg_edge,
    msg_horas,
    msg_kalshi_todas,
    msg_pico,
    msg_prob,
    msg_resumen,
    msg_status_wb,
)
from charts import grafico_completo, grafico_historial
from cyclones import msg_huracanes
from cities import (
    DEFAULT_CITY_ID,
    KALSHI_CITIES,
    get_city,
    lista_ciudades_texto,
    resolve_city,
    resolve_city_from_args,
    resolve_location,
)
from api import WindBorneError, fetch_all_cities_forecast
from observations import fetch_all_metar
from config import (
    DOCS_URL,
    INTERVALO_MONITOR,
    OPEN_METEO_ENABLED,
    PREFETCH_ENABLED,
    PREFETCH_INTERVAL,
    TELEGRAM_TOKEN,
)
from extras import (
    comparar_3km,
    comparar_modelos,
    msg_clima_completo,
    msg_lluvia,
    msg_min,
    msg_nubes,
    msg_tormenta,
    msg_variable,
    msg_vars,
)
from fallback_om import msg_fallback_resumen, msg_manana
from gemini import lanzar_gemini_async
from telegram_io import enviar, enviar_foto

_monitor_activo = {}
_ciudad_activa = {}

COMANDOS_BASE = {
    "resumen",
    "ahora",
    "pico",
    "horas",
    "prob",
    "edge",
    "kalshi",
    "grafico",
    "chart",
    "max",
    "min",
    "lluvia",
    "tormenta",
    "nubes",
    "3km",
    "sol",
    "radiacion",
    "clima",
    "modelos",
    "historial",
    "vars",
    "gemini",
    "ia",
    "todo",
    "all",
    "huracanes",
    "ciclones",
    "docs",
    "monitor",
    "ciudad",
    "ciudades",
    "help",
    "start",
    "todas",
    "usa",
    "refresh",
    "actualizar",
    "status",
    "estado",
    "manana",
    "mañana",
    "tomorrow",
}


def _help(ciudad_id=None):
    cid = ciudad_id or DEFAULT_CITY_ID
    city = get_city(cid)
    return (
        f"<b>WindBorne Monitor v4 — Kalshi USA</b>\n"
        f"Ciudad activa: <b>{city['nombre']}</b> ({city.get('serie') or 'USA'})\n\n"
        "<b>Temperatura / Kalshi</b>\n"
        "/resumen /ahora /pico /max /min /prob /horas /edge\n"
        "/manana → pico de mañana (Open-Meteo)\n"
        "/status → estado API / cuota\n"
        "/docs → referencia técnica de la API WindBorne\n\n"
        "<b>Precip / tormenta / nubes</b>\n"
        "/lluvia → precip 3h\n"
        "/tormenta → CAPE, tipo precip, nubes bajas\n"
        "/nubes → capas + radiación\n\n"
        "<b>Más datos</b>\n"
        "/clima /sol /3km /modelos /vars /gemini /historial\n"
        "/huracanes → ciclones activos (AL/EP/CP) · /huracanes global → todas las cuencas\n\n"
        "<b>Multi-ciudad (KXHIGH)</b>\n"
        "/ciudades · /kalshi · /refresh\n"
        "/ciudad miami · /pico nyc\n\n"
        "<b>Cualquier ciudad USA</b>\n"
        "Escribe: <code>boise</code> · /resumen tampa florida\n\n"
        "<b>Alertas</b> /monitor on|off\n\n"
        "<i>Si WindBorne sin cuota → fallback Open-Meteo en /resumen y /manana.</i>"
    )


def _ciudad_chat(chat_id):
    return _ciudad_activa.get(chat_id, DEFAULT_CITY_ID)


def _parse(texto):
    partes = texto.strip().split()
    if not partes:
        return "", None, []

    raw = partes[0].lower()
    cmd = raw.lstrip("/")
    args = partes[1:]

    city_id, args = resolve_city_from_args(args)

    if cmd in KALSHI_CITIES:
        return "resumen", cmd, args

    if cmd not in COMANDOS_BASE:
        alias = resolve_city(cmd)
        if alias:
            return "resumen", alias, args
        lugar = resolve_location(" ".join(partes).lstrip("/"))
        if lugar:
            return "resumen", lugar["id"], []
        lugar = resolve_location(cmd)
        if lugar:
            return "resumen", lugar["id"], args

    return cmd, city_id, args


def _resolver_ciudad(chat_id, city_id):
    if city_id:
        return get_city(city_id)
    return get_city(_ciudad_chat(chat_id))


def _enviar_kalshi_todas(chat_id, force=False):
    if force:
        enviar(chat_id, "⏳ Actualizando 20 ciudades (1 llamada API)...", parse_mode=None)
    msg = msg_kalshi_todas(force=force)
    for i in range(0, len(msg), 4000):
        enviar(chat_id, msg[i : i + 4000])


def _prefetch_loop():
    # Primera espera: no quemar cuota al arrancar
    time.sleep(30)
    while True:
        if not PREFETCH_ENABLED:
            time.sleep(PREFETCH_INTERVAL)
            continue
        try:
            from api import api_status

            if api_status().get("quota_exhausted"):
                print("Prefetch pausado: cuota WindBorne agotada")
                # Solo METAR (gratis) mientras WB esté muerto
                fetch_all_metar(force=True)
                time.sleep(PREFETCH_INTERVAL)
                continue
            fetch_all_cities_forecast(force=True)
            fetch_all_metar(force=True)
            print("✓ WM-6 + METAR actualizados (20 ciudades)")
        except Exception as e:
            print(f"Prefetch error: {e}")
        time.sleep(PREFETCH_INTERVAL)


def _enviar_con_fallback(chat_id, city, err):
    """Si WB falla, manda Open-Meteo en vez de solo el error."""
    if not OPEN_METEO_ENABLED:
        enviar(chat_id, f"⚠️ WindBorne: {err}", parse_mode=None)
        return
    try:
        enviar(chat_id, msg_fallback_resumen(city=city, error_wb=str(err)))
    except Exception as e2:
        enviar(chat_id, f"⚠️ WindBorne: {err}\nFallback OM falló: {e2}", parse_mode=None)


def handle(texto, chat_id):
    cmd, city_id, args = _parse(texto)

    if cmd in ("start", "help"):
        enviar(chat_id, _help(_ciudad_chat(chat_id)), parse_mode=None)
        return

    if cmd in ("status", "estado"):
        enviar(chat_id, msg_status_wb())
        return

    if cmd == "docs":
        enviar(
            chat_id,
            f"<b>Doc técnica — WindBorne API</b>\n{DOCS_URL}",
        )
        return

    if cmd == "ciudades":
        enviar(chat_id, lista_ciudades_texto(), parse_mode=None)
        return

    if cmd == "ciudad":
        if not args:
            cid = _ciudad_chat(chat_id)
            c = get_city(cid)
            enviar(
                chat_id,
                f"Ciudad activa: <b>{c['nombre']}</b> ({c['serie']})\n"
                f"Usa /ciudad miami para cambiar.",
                parse_mode=None,
            )
            return
        nombre = " ".join(args)
        lugar = resolve_location(nombre)
        if not lugar:
            enviar(
                chat_id,
                "Ciudad no reconocida. Prueba /ciudades (Kalshi) o escribe "
                "el nombre completo: /ciudad salt lake city",
                parse_mode=None,
            )
            return
        _ciudad_activa[chat_id] = lugar["id"]
        c = get_city(lugar["id"])
        enviar(
            chat_id,
            f"✅ Ciudad activa: <b>{c['nombre']}</b> ({c['serie']} · {c['station']})",
            parse_mode=None,
        )
        return

    if cmd.startswith("monitor") or cmd == "monitor":
        if args and args[0] == "off":
            _monitor_activo.pop(chat_id, None)
            enviar(chat_id, "🔕 Monitor apagado.", parse_mode=None)
        else:
            _monitor_activo[chat_id] = _ciudad_chat(chat_id)
            c = get_city(_ciudad_chat(chat_id))
            enviar(
                chat_id,
                f"🔔 Monitor ON — {c['nombre']}. Alerta si el pico cambia ≥1°F.",
                parse_mode=None,
            )
        return

    city = _resolver_ciudad(chat_id, city_id)

    if cmd in ("manana", "mañana", "tomorrow"):
        try:
            enviar(chat_id, msg_manana(city=city))
        except Exception as e:
            enviar(chat_id, f"Error mañana: {e}", parse_mode=None)
        return

    if cmd in ("refresh", "actualizar"):
        _enviar_kalshi_todas(chat_id, force=True)
        return

    if cmd in ("kalshi", "todas", "usa"):
        if city_id:
            try:
                a = analizar(city)
                if a:
                    log_peak(a)
                    enviar(chat_id, msg_prob(a))
                else:
                    enviar(chat_id, f"Sin datos para {city['nombre']}.", parse_mode=None)
            except Exception as e:
                enviar(chat_id, f"Error: {e}", parse_mode=None)
        else:
            _enviar_kalshi_todas(chat_id)
        return

    if cmd in ("max",):
        enviar(chat_id, msg_variable("max_temperature_2m_3h", city=city))
        return
    if cmd == "min":
        enviar(chat_id, msg_min(city=city))
        return
    if cmd == "lluvia":
        enviar(chat_id, "⏳ Consultando precipitación...", parse_mode=None)
        enviar(chat_id, msg_lluvia(city=city))
        return
    if cmd == "tormenta":
        enviar(chat_id, "⏳ Analizando convección (~25s, 5 variables)...", parse_mode=None)
        try:
            enviar(chat_id, msg_tormenta(city=city))
        except WindBorneError as e:
            enviar(chat_id, f"⚠️ {e}", parse_mode=None)
        return
    if cmd == "nubes":
        enviar(chat_id, "⏳ Consultando nubes y radiación...", parse_mode=None)
        enviar(chat_id, msg_nubes(city=city))
        return
    if cmd == "3km":
        enviar(chat_id, "⏳ Comparando WM-6 vs 3km...", parse_mode=None)
        enviar(chat_id, comparar_3km(city=city))
        return
    if cmd in ("gemini", "ia"):
        try:
            a = analizar(city)
            if a:
                log_peak(a)
            lanzar_gemini_async(chat_id, city=city, analisis_temp=a)
        except Exception as e:
            enviar(chat_id, f"Error: {e}", parse_mode=None)
        return
    if cmd in ("sol", "radiacion"):
        try:
            enviar(chat_id, msg_variable("solar_radiation_downwards_3h", city=city))
        except Exception:
            enviar(chat_id, msg_variable("short_wave_radiation", city=city))
        return
    if cmd == "clima":
        enviar(chat_id, msg_clima_completo(city=city))
        return
    if cmd == "modelos":
        enviar(chat_id, "⏳ Comparando modelos (~40s)...", parse_mode=None)
        enviar(chat_id, comparar_modelos(city=city))
        return
    if cmd in ("huracanes", "ciclones"):
        global_ = bool(args) and args[0].lower() in ("global", "todas", "all")
        enviar(chat_id, "⏳ Consultando ciclones tropicales...", parse_mode=None)
        try:
            enviar(chat_id, msg_huracanes(global_=global_))
        except Exception as e:
            enviar(chat_id, f"Error huracanes: {e}", parse_mode=None)
        return
    if cmd == "historial":
        img = grafico_historial(city["id"])
        if img:
            enviar_foto(chat_id, img, f"Evolución pico — {city['nombre']}")
        else:
            enviar(chat_id, "Necesitas más consultas primero. Usa /pico varias veces.", parse_mode=None)
        return
    if cmd == "vars":
        enviar(chat_id, msg_vars(city=city))
        return

    if cmd in ("todo", "all"):
        enviar(chat_id, f"📦 Cargando todo WindBorne — {city['nombre']}...", parse_mode=None)
        try:
            a = analizar(city)
            if a:
                log_peak(a)
                enviar(chat_id, msg_resumen(a))
                enviar(chat_id, msg_pico(a))
                enviar(chat_id, msg_variable("max_temperature_2m_3h", city=city))
                enviar(chat_id, msg_min(city=city))
                enviar(chat_id, msg_lluvia(city=city))
                enviar(chat_id, msg_tormenta(city=city))
                enviar(chat_id, comparar_3km(city=city))
                enviar(chat_id, msg_clima_completo(city=city))
                enviar(chat_id, msg_nubes(city=city))
                img = grafico_completo(a)
                if img:
                    enviar_foto(chat_id, img, f"WindBorne WM-6 — {city['nombre']}")
                hist = grafico_historial(city["id"])
                if hist:
                    enviar_foto(chat_id, hist, f"Historial — {city['nombre']}")
                lanzar_gemini_async(chat_id, city=city, analisis_temp=a)
        except Exception as e:
            enviar(chat_id, f"Error: {e}", parse_mode=None)
        return

    try:
        a = analizar(city)
        if not a:
            if OPEN_METEO_ENABLED and cmd in ("resumen", "ahora", "pico", ""):
                _enviar_con_fallback(chat_id, city, "sin puntos para hoy")
            else:
                enviar(
                    chat_id,
                    f"Sin datos WindBorne para {city['nombre']} hoy.",
                    parse_mode=None,
                )
            return

        log_peak(a)

        if cmd == "resumen":
            enviar(chat_id, msg_resumen(a))
        elif cmd == "ahora":
            enviar(chat_id, msg_ahora(a))
        elif cmd == "pico":
            enviar(chat_id, msg_pico(a))
        elif cmd == "horas":
            enviar(chat_id, msg_horas(a))
        elif cmd == "prob":
            enviar(chat_id, msg_prob(a))
        elif cmd == "edge":
            enviar(chat_id, msg_edge(a))
        elif cmd in ("grafico", "chart"):
            img = grafico_completo(a)
            if img:
                enviar_foto(chat_id, img, f"WindBorne WM-6 — {city['nombre']}")
            else:
                enviar(chat_id, "No hay datos para graficar.", parse_mode=None)
        else:
            enviar(chat_id, "Comando no reconocido. /help", parse_mode=None)

    except WindBorneError as e:
        if cmd in ("resumen", "ahora", "pico", "prob", "horas", "edge"):
            _enviar_con_fallback(chat_id, city, e)
        else:
            enviar(chat_id, f"⚠️ WindBorne: {e}", parse_mode=None)
    except Exception as e:
        enviar(chat_id, f"Error: {e}", parse_mode=None)


def _chequear_alertas():
    for chat_id, city_id in list(_monitor_activo.items()):
        try:
            city = get_city(city_id)
            a = analizar(city)
            if not a:
                continue
            log_peak(a)
            cambio = detectar_cambio_pico(a["pico"]["temp_f"], city_id)
            if not cambio:
                continue
            msg = (
                f"<b>⚠️ CAMBIO EN EL PICO — {city['nombre']}</b>\n"
                f"Antes: {cambio['anterior']}°F\n"
                f"Ahora: <b>{cambio['nuevo']}°F</b>\n"
                f"Cambio: {cambio['diff']:+.1f}°F\n\n"
                f"P≥97: {a['probs_pico'].get(97, 0)}%  "
                f"P≥98: {a['probs_pico'].get(98, 0)}%"
            )
            enviar(chat_id, msg)
        except Exception as e:
            print(f"Error monitor {chat_id}: {e}")


def main():
    from api import api_status
    from config import WIND_KEY

    print(f"🎈 WindBorne Monitor v4 — {len(KALSHI_CITIES)} ciudades Kalshi KXHIGH")
    print(f"Key: …{(WIND_KEY or '')[-8:]} | prefetch={PREFETCH_ENABLED} cada {PREFETCH_INTERVAL}s")
    print(f"Open-Meteo fallback: {OPEN_METEO_ENABLED}")
    if PREFETCH_ENABLED:
        threading.Thread(target=_prefetch_loop, daemon=True).start()
    offset = 0
    ultimo_monitor = 0

    while True:
        try:
            if time.time() - ultimo_monitor >= INTERVALO_MONITOR:
                st = api_status()
                if not st.get("quota_exhausted"):
                    _chequear_alertas()
                ultimo_monitor = time.time()

            r = requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                params={"offset": offset, "timeout": 25},
                timeout=35,
            )
            for upd in r.json().get("result", []):
                offset = upd["update_id"] + 1
                if "message" in upd and "text" in upd["message"]:
                    handle(upd["message"]["text"], upd["message"]["chat"]["id"])
        except Exception as e:
            print(f"Loop error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()