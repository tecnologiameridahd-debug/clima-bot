# Publicar el bot de clima en Render (sin PC prendida)

Así el bot de Telegram corre **en internet 24/7**. Apagas la PC y sigue.

> Importante: en el PC **cierra** `iniciar_bot.bat` cuando el de la nube esté Live.
> Solo puede haber **una** instancia del mismo bot (si no: error Conflict en Telegram).

---

## Qué hace el servidor

| Pieza | Rol |
|-------|-----|
| `server.py` | HTTP `/api/health` + arranca el bot |
| `clima_bot.py` | El bot de Telegram (long polling) |
| Render free | Hosting |
| cron-job.org | Despierta el servicio cada 10 min (plan free se duerme) |

---

## Paso 1 — Subir código a GitHub

En PowerShell:

```powershell
cd C:\Users\Alberto\weather_data
git init
git add server.py clima_bot.py requirements.txt render.yaml .gitignore PUBLICAR_RENDER.md config_local.example.py
git add multi_source_scraper.py get_cli_report.py scrape_nws.py watch_nws.py 2>$null
git commit -m "Clima bot listo para Render"
```

Crea un repo en GitHub (ej. `clima-bot`) y:

```powershell
git branch -M main
git remote add origin https://github.com/TU_USUARIO/clima-bot.git
git push -u origin main
```

**NO subas** `config_local.py` (ya está en `.gitignore`).

---

## Paso 2 — Crear Web Service en Render

1. https://dashboard.render.com → **New** → **Web Service**
2. Conecta el repo `clima-bot`
3. Configura:

| Campo | Valor |
|-------|--------|
| Name | `clima-bot` |
| Runtime | Python 3 |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `uvicorn server:app --host 0.0.0.0 --port $PORT` |
| Plan | **Free** |

4. **Environment** → añade (copia de tu `config_local.py` local):

| Key | Value |
|-----|--------|
| `TELEGRAM_TOKEN` | token del BotFather (bot clima) |
| `WINDBORNE_API_KEY` | tu key wb_… |
| `TOMORROW_KEY` | (si la usas) |
| `GEMINI_API_KEY` | (si usas IA) |
| `VISUALCROSSING_KEY` | (opcional) |
| `XAI_API_KEY` | (opcional Grok) |

5. **Create Web Service** → espera **Live** (3–8 min).

URL tipo: `https://clima-bot-xxxx.onrender.com`

Comprueba:

```text
https://clima-bot-xxxx.onrender.com/api/health
```

Debe decir `"ok": true` y `bot_started: true`.

---

## Paso 3 — Keep-alive (imprescindible en Free)

Render free **se duerme** ~15 min sin visitas → el bot deja de contestar.

1. Entra a https://cron-job.org (gratis)
2. Create cron job:
   - URL: `https://TU-APP.onrender.com/api/health`
   - Every: **10 minutes**
3. Save

Con eso el bot se mantiene despierto casi siempre.

---

## Paso 4 — Apagar el bot en la PC

1. Cierra la ventana del bot / `iniciar_bot.bat`
2. Prueba en Telegram un comando (`/all` o el que uses)
3. Debe contestar **sin tu PC**

Si ves *Conflict: terminated by other getUpdates*:  
todavía hay otra copia corriendo (PC u otro Render). Cierra todas menos la de la nube.

---

## WindBorne Monitor (otro bot)

Es un bot **aparte** (`windborne_monitor`). Se publica igual:

- Start: otro servicio Render o el mismo repo con otro entrypoint
- Token distinto: `WB_TELEGRAM_TOKEN`
- Key: `WINDBORNE_API_KEY`

Si quieres, se deja un `server.py` igual en esa carpeta.

---

## Resumen

```text
GitHub (código sin secrets)
  → Render Web Service + env vars
  → cron-job.org cada 10 min a /api/health
  → cierra el bot en la PC
  → Telegram funciona 24/7
```

---

## Si algo falla

| Problema | Solución |
|----------|----------|
| health ok pero no contesta | Revisa `TELEGRAM_TOKEN` en Render; mira Logs |
| Conflict getUpdates | Cierra bot en PC y solo 1 servicio Render |
| Se duerme | cron-job.org no está activo |
| Build fail matplotlib | Logs de build; a veces tarda más en free |
| Sin IA | Falta `GEMINI_API_KEY` en Environment |

---

## Coste

- Render Free: $0 (se duerme sin keep-alive)
- cron-job.org Free: $0
- Si un día quieres sin dormir: Render **Starter** o worker de pago
