@echo off
chcp 65001 >nul
cd /d "%~dp0"
title SecondX - High Precision Infrastructure Telemetry Agent
echo ======================================================================
echo          SecondX - High Precision Infrastructure Telemetry Agent
echo                     Developed by Ilhan Kocaslan
echo ======================================================================
echo.
set "PY=py -3"
where py >nul 2>nul || set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
%PY% -c "import psutil, influxdb_client" >nul 2>nul || (
  echo Installing requirements...
  %PY% -m pip install -r requirements.txt || goto hata
)
%PY% SecondX.py %*
goto son
:hata
echo Python 3.8+ is required: https://www.python.org/downloads/
:son
pause
