import threading
import time
from datetime import datetime

import requests

from analysis import analizar
from cities import DEFAULT_CITY_ID, get_city
from config import (
    GEMINI_API_KEY,
    GEMINI_CACHE_TTL,
    GEMINI_FALLBACK_MODELS,
    GEMINI_MIN_INTERVAL,
    GEMINI_MODEL,
    GEMINI_TIMEOUT,
)
from extras import recolectar_reporte, texto_reporte_plano
from telegram_io import enviar, enviar_largo

_GEMINI_CACHE = {"texto": None, "fetched_at": 0.0, "key": None}
_GEMINI_LOCK = threading.Lock()
_gemini_running = False


def _modelos_intentos():
    modelos = [GEMINI_MODEL]
    for m in GEMINI_FALLBACK_MODELS:
        if m not in modelos:
            modelos.append(m)
    return modelos


def _extraer_texto(data):
    candidates = data.get("candidates", [])
    if not candidates:
        return None
    parts = candidates[0].get("content", {}).get("parts", [])
    textos = [p["text"].strip() for p in parts if p.get("text")]
    return textos[-1] if textos else None


def _error_429(detalle=""):
    return (
        "⏳ Gemini sin quota (429).\n\n"
        f"Modelo: {GEMINI_MODEL}\n"
        "Prueba más tarde o usa gemini-2.5-flash-lite en config.\n\n"
        f"Detalle: {detalle[:200]}"
    )


def consultar_gemini(prompt):
    if not GEMINI_API_KEY:
        return None, (
            "❌ Falta GEMINI_API_KEY.\n"
            "Configúrala en weather_data/config_local.py o $env:GEMINI_API_KEY"
        ), None

    ultimo_error = ""
    for intento, modelo in enumerate(_modelos_intentos()):
        if intento > 0:
            time.sleep(min(3 * intento, 10))

        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{modelo}:generateContent"
        )
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": 1000,
                "temperature": 0.3,
                "thinkingConfig": {"thinkingBudget": 0},
            },
        }
        try:
            r = requests.post(
                url,
                params={"key": GEMINI_API_KEY},
                json=payload,
                timeout=(5, GEMINI_TIMEOUT),
            )
        except requests.exceptions.Timeout:
            return None, f"⏱ Gemini tardó más de {GEMINI_TIMEOUT}s.", None

        if r.status_code == 429:
            ultimo_error = r.text[:300]
            continue
        if r.status_code != 200:
            ultimo_error = r.text[:300]
            continue

        texto = _extraer_texto(r.json())
        if not texto or len(texto) < 80:
            ultimo_error = "respuesta vacía o corta"
            continue
        return texto, None, modelo

    if "429" in ultimo_error or "quota" in ultimo_error.lower():
        return None, _error_429(ultimo_error), None
    return None, f"Error Gemini: {ultimo_error[:250]}", None


def _armar_prompt(reporte):
    city = reporte["city"]
    tz = city["tz"]
    ahora = datetime.now(tz).strftime("%Y-%m-%d %H:%M %Z")
    datos = texto_reporte_plano(reporte)
    bloque_3km = reporte.get("texto_3km") or ""
    if bloque_3km:
        limpio = bloque_3km.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")
        datos += f"\n\nCOMPARACION_3KM:\n{limpio}"
    serie = city.get("serie") or "KXHIGH"

    return f"""Eres un analista experto en clima y mercados de predicción Kalshi.
El usuario opera contratos de temperatura MÁXIMA diaria ({serie}) en {city['nombre']}, USA.
Los mercados KXHIGH liquidan con el máximo OBSERVADO oficial del día (estación aeropuerto + reporte climatológico NWS), NO con pronósticos.
Hoy es {ahora}.

Todos los datos siguientes vienen de WindBorne WeatherMesh-6 (IA) y METAR en tiempo casi real:

{datos}

INSTRUCCIONES:
- Responde en español, claro y directo (máximo 400 palabras).
- Analiza el pico térmico pronosticado y su incertidumbre (distribución IA).
- Evalúa si lluvia/tormenta PM o nubes bajas pueden REDUCIR el máximo real vs el pico del modelo.
- Compara WM-6 vs WM-6-3km si aparece en el bloque COMPARACION_3KM.
- Comenta METAR observado vs pronóstico si está disponible.
- Menciona timing del pico (mañana vs tarde) y factores que lo mueven.
- NO recomiendes apostar YES/NO ni umbrales concretos.
- Texto plano para Telegram, sin markdown ni asteriscos."""


def analisis_gemini(city=None, analisis_temp=None):
    global _GEMINI_CACHE

    city = city or get_city(DEFAULT_CITY_ID)
    cache_key = f"{city['id']}_{datetime.now(city['tz']).strftime('%Y%m%d_%H')}"

    if (
        _GEMINI_CACHE.get("texto")
        and _GEMINI_CACHE.get("key") == cache_key
        and time.time() - _GEMINI_CACHE.get("fetched_at", 0) < GEMINI_CACHE_TTL
    ):
        return _GEMINI_CACHE["texto"]

    if (
        _GEMINI_CACHE.get("texto")
        and time.time() - _GEMINI_CACHE.get("fetched_at", 0) < GEMINI_MIN_INTERVAL
    ):
        return _GEMINI_CACHE["texto"] + "\n\n(reciente — espera antes de repetir)"

    if not _GEMINI_LOCK.acquire(blocking=False):
        return "⏳ Ya hay un análisis Gemini en curso."

    try:
        a = analisis_temp or analizar(city=city)
        reporte = recolectar_reporte(city=city, analisis_temp=a)
        try:
            from extras import comparar_3km

            reporte["texto_3km"] = comparar_3km(city=city)
        except Exception:
            pass
        prompt = _armar_prompt(reporte)
        texto, error, modelo = consultar_gemini(prompt)
        if error:
            return error

        resultado = f"🧠 Análisis Gemini ({modelo or GEMINI_MODEL})\n\n{texto}"
        _GEMINI_CACHE.update({
            "texto": resultado,
            "key": cache_key,
            "fetched_at": time.time(),
        })
        return resultado
    finally:
        _GEMINI_LOCK.release()


def _gemini_background(chat_id, city, analisis_temp):
    global _gemini_running
    try:
        resultado = analisis_gemini(city=city, analisis_temp=analisis_temp)
        enviar_largo(chat_id, resultado, parse_mode=None)
    except Exception as e:
        enviar(chat_id, f"❌ Error Gemini: {e}", parse_mode=None)
    finally:
        _gemini_running = False


def lanzar_gemini_async(chat_id, city=None, analisis_temp=None):
    global _gemini_running
    if _gemini_running:
        enviar(chat_id, "⏳ Gemini ya está analizando. Espera a que termine.", parse_mode=None)
        return

    _gemini_running = True
    enviar(
        chat_id,
        f"🧠 Gemini analizando reporte WindBorne completo (~{GEMINI_TIMEOUT}s máx)...",
        parse_mode=None,
    )
    threading.Thread(
        target=_gemini_background,
        args=(chat_id, city, analisis_temp),
        daemon=True,
    ).start()