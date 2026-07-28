@echo off
cd /d "%~dp0"
title WindBorne Monitor v4
echo ================================
echo  WindBorne Monitor v4 - Kalshi
echo ================================
echo.
if not exist config_local.py (
  echo TIP: copia config_local.example.py a config_local.py
  echo y pega WINDBORNE_API_KEY
  echo.
)
echo Cerrando copias anteriores...
powershell -NoProfile -Command ^
  "Get-CimInstance Win32_Process -Filter \"Name = 'python.exe'\" | ForEach-Object { if ($_.CommandLine -like '*windborne_monitor*bot.py*') { Stop-Process -Id $_.ProcessId -Force -EA SilentlyContinue } }"
timeout /t 2 /nobreak >nul
echo Arrancando...
python bot.py
echo.
echo Bot detenido.
pause
