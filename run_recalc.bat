@echo off
:: PLM Ticker Watcher — Daily Recalculation Launcher
:: --------------------------------------------------
:: Run this file directly (double-click) or point Task Scheduler at it.
:: It activates the venv, runs the recalc, and logs everything to daily_recalc.log

cd /d "%~dp0"

echo [%DATE% %TIME%] Starting PLM Ticker Watcher daily recalc...

:: Activate virtual environment
call venv\Scripts\activate.bat
if errorlevel 1 (
    echo ERROR: Could not activate venv. Is it at .\venv? Run: python -m venv venv
    pause
    exit /b 1
)

:: Run the recalculator
python daily_recalc.py %*

echo [%DATE% %TIME%] Recalc finished. See daily_recalc.log for details.
