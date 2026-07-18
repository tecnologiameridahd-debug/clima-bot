"""
Servidor para Render / nube: health HTTP + bot de clima en segundo plano.

Uso local:
  python server.py

Uso Render (Start Command):
  uvicorn server:app --host 0.0.0.0 --port $PORT
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone

from fastapi import FastAPI

app = FastAPI(title="ClimaBot", version="1.1.0")

_bot_started = False
_bot_error: str | None = None
_started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")


def _run_bot() -> None:
    global _bot_error
    try:
        from clima_bot import TELEGRAM_TOKEN, main

        if not TELEGRAM_TOKEN:
            _bot_error = "Falta TELEGRAM_TOKEN (env o config_local)"
            print(f"[server] ERROR: {_bot_error}")
            return
        print("[server] Arrancando clima_bot.main()…")
        main()
    except Exception as e:
        _bot_error = str(e)[:300]
        print(f"[server] Bot crash: {e}")
        time.sleep(5)


def _ensure_bot() -> None:
    global _bot_started
    if _bot_started:
        return
    _bot_started = True
    t = threading.Thread(target=_run_bot, name="clima-bot", daemon=True)
    t.start()


@app.on_event("startup")
def on_startup() -> None:
    _ensure_bot()


@app.get("/")
def root():
    return {
        "app": "clima-bot",
        "status": "alive",
        "bot": "running" if _bot_started and not _bot_error else "error",
        "error": _bot_error,
        "hint": "Telegram long-polling. Keep-alive: GET /api/health cada 10 min.",
    }


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "app": "clima-bot",
        "bot_started": _bot_started,
        "bot_error": _bot_error,
        "started_at": _started_at,
        "utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "has_telegram_token": bool(
            os.environ.get("TELEGRAM_TOKEN")
            or os.environ.get("CLIMA_TELEGRAM_TOKEN")
            or _token_from_local()
        ),
    }


def _token_from_local() -> bool:
    try:
        from clima_bot import TELEGRAM_TOKEN

        return bool(TELEGRAM_TOKEN)
    except Exception:
        return False


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8790"))
    _ensure_bot()
    uvicorn.run(app, host="0.0.0.0", port=port)
