@echo off
title SecondX - High Precision Infrastructure Telemetry Agent
cls
echo ======================================================================
echo          SecondX - High Precision Infrastructure Telemetry Agent
echo                     Developed by Ilhan Kocaslan
echo ======================================================================
echo.

py -3.11 SecondX.py
if %errorlevel% neq 0 (
    python SecondX.py
)

pause
