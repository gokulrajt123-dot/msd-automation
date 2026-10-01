@echo off
title MSD GMA stock
cd /d "%~dp0"

rem ---- First run only: create Python environment and install packages ----
if not exist ".venv\Scripts\python.exe" (
    echo Setting up for the first time, please wait...
    py -3 -m venv .venv 2>nul || python -m venv .venv
    if not exist ".venv\Scripts\python.exe" (
        echo ERROR: Python is not installed. Install it from https://www.python.org/downloads/
        pause
        exit /b 1
    )
    ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
    ".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt
    if not errorlevel 1 ".venv\Scripts\python.exe" -m playwright install chromium
    if errorlevel 1 (
        echo ERROR: Could not install required packages. Check your internet connection.
        rmdir /s /q .venv
        pause
        exit /b 1
    )
)

rem ---- First run only: ask for MSD login details ----
if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo.
    echo Notepad will open now. Type your MSD username and password,
    echo then click File - Save, and close Notepad.
    echo.
    pause
    notepad ".env"
)

echo Starting GMA stock...
".venv\Scripts\python.exe" msd_bot.py --report "GMA stock"
if errorlevel 1 pause
