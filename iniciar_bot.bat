@echo off
cd /d "%~dp0"
title Clima Bot Denver (local)
echo ================================
echo  Bot clima Denver — SOLO PC
echo ================================
echo.
echo Si el bot ya corre en Render, CIERRA esta ventana
echo (Telegram no permite 2 copias a la vez).
echo.
echo En la nube: ver PUBLICAR_RENDER.md
echo.
set PYTHONUNBUFFERED=1
python -u clima_bot.py
pause
