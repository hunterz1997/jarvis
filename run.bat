@echo off
setlocal enabledelayedexpansion
title JARVIS Launcher
color 0B
chcp 65001 >nul
cls

echo.
echo  ████████████████████████████████████████
echo   J.A.R.V.I.S  -  Just A Rather Very
echo      Intelligent System  v1.0
echo  ████████████████████████████████████████
echo.

:: ── Check Python ───────────────────────────────────────────────
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo  [ERROR] Python not found. Install Python 3.11+ from python.org
    pause
    exit /b 1
)

:: ── Check Node.js ──────────────────────────────────────────────
node --version >nul 2>&1
if %errorlevel% neq 0 (
    echo  [WARN] Node.js not found. WhatsApp bridge will not start.
    echo         Install Node.js 18+ from nodejs.org to enable WhatsApp.
    echo.
    set SKIP_WHATSAPP=1
) else (
    set SKIP_WHATSAPP=0
)

:: ── Create venv if needed ───────────────────────────────────────
if not exist "%~dp0.venv" (
    echo  [SETUP] Creating Python virtual environment...
    python -m venv "%~dp0.venv"
    echo  [SETUP] Installing Python dependencies (first-time only, takes ~2 mins)...
    "%~dp0.venv\Scripts\pip.exe" install --pre -r "%~dp0requirements.txt" --quiet
    echo  [SETUP] Dependencies installed.
    echo.
)

:: ── Install Node deps if needed ─────────────────────────────────
if "%SKIP_WHATSAPP%"=="0" (
    if not exist "%~dp0whatsapp_bridge\node_modules" (
        echo  [SETUP] Installing WhatsApp bridge dependencies...
        cd /d "%~dp0whatsapp_bridge"
        call npm install --quiet 2>nul
        cd /d "%~dp0"
        echo  [SETUP] WhatsApp ready.
    )
)

:: ── Start WhatsApp Bridge ───────────────────────────────────────
if "%SKIP_WHATSAPP%"=="0" (
    echo  [START] Launching WhatsApp bridge...
    start "JARVIS - WhatsApp Bridge" cmd /k "cd /d "%~dp0whatsapp_bridge" && node server.js"
    timeout /t 2 /nobreak >nul
)

:: ── Check if API key is configured ─────────────────────────────
:: Check for ANTHROPIC_API_KEY that starts with sk-ant- in .env
set HAS_KEY=0
if exist "%~dp0.env" (
    for /f "tokens=1,* delims==" %%A in ('findstr /i "ANTHROPIC_API_KEY" "%~dp0.env"') do (
        set KEYVAL=%%B
        if "!KEYVAL:~0,7!"=="sk-ant-" set HAS_KEY=1
    )
)

:: ── Launch appropriate mode ─────────────────────────────────────
echo.
if "%HAS_KEY%"=="1" (
    echo  [START] API key found. Launching Jarvis...
    echo.
    echo  ════════════════════════════════════════
    echo   Jarvis will open at: http://localhost:8000
    echo   Press Ctrl+C to stop.
    echo  ════════════════════════════════════════
    echo.
    "%~dp0.venv\Scripts\python.exe" "%~dp0main.py"
) else (
    echo  [SETUP] First run detected. Opening setup wizard...
    echo.
    echo  ════════════════════════════════════════
    echo   Setup page: http://localhost:8000
    echo   You will need an Anthropic API key.
    echo   Get one free at: console.anthropic.com
    echo  ════════════════════════════════════════
    echo.
    "%~dp0.venv\Scripts\python.exe" "%~dp0setup.py"
)

pause
