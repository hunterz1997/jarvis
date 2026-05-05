@echo off
REM ════════════════════════════════════════════════════════════════
REM JARVIS — Install / Remove Windows auto-start
REM ════════════════════════════════════════════════════════════════
REM Drops (or removes) a shortcut in the user's Startup folder
REM that runs start_jarvis_silent.ps1 at every login.
REM
REM Usage:  setup_autostart.bat            (install)
REM         setup_autostart.bat remove     (uninstall)
REM ════════════════════════════════════════════════════════════════
setlocal

set SCRIPT_DIR=%~dp0
set PS1_PATH=%SCRIPT_DIR%start_jarvis_silent.ps1
set HELPER_PS1=%SCRIPT_DIR%_install_shortcut.ps1
set STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
set SHORTCUT_PATH=%STARTUP_DIR%\Jarvis Auto-Start.lnk

if /I "%~1"=="remove"    goto :uninstall
if /I "%~1"=="uninstall" goto :uninstall

:install
echo.
echo  Installing Jarvis auto-start...
echo  Launcher script:  %PS1_PATH%
echo  Startup shortcut: %SHORTCUT_PATH%
echo.

if not exist "%PS1_PATH%" (
    echo  [ERROR] Cannot find: %PS1_PATH%
    echo          Make sure you're running this from the deploy\ folder.
    pause
    exit /b 1
)

if not exist "%HELPER_PS1%" (
    echo  [ERROR] Cannot find: %HELPER_PS1%
    pause
    exit /b 1
)

REM Run helper script that creates the .lnk
powershell -NoProfile -ExecutionPolicy Bypass -File "%HELPER_PS1%" -Ps1Path "%PS1_PATH%" -ShortcutPath "%SHORTCUT_PATH%" -WorkingDir "%SCRIPT_DIR%"

if exist "%SHORTCUT_PATH%" (
    echo.
    echo  ════════════════════════════════════════════════
    echo   SUCCESS. Jarvis will auto-start on next login.
    echo  ════════════════════════════════════════════════
    echo.
    echo   - Bridge + API will run silently in background
    echo   - The UI ^(Chrome window^) only opens when you
    echo     double-click launcher.pyw
    echo   - To remove auto-start, run:
    echo         setup_autostart.bat remove
    echo.
    echo   Test it now without rebooting:
    echo     powershell -ExecutionPolicy Bypass -File "%PS1_PATH%"
    echo.
) else (
    echo.
    echo  [ERROR] Shortcut creation failed.
    echo          Try right-clicking this .bat and "Run as administrator".
    echo.
)
pause
exit /b 0

:uninstall
echo.
echo  Removing Jarvis auto-start...
if exist "%SHORTCUT_PATH%" (
    del /F /Q "%SHORTCUT_PATH%"
    if exist "%SHORTCUT_PATH%" (
        echo  [ERROR] Could not delete %SHORTCUT_PATH%
    ) else (
        echo  Removed: %SHORTCUT_PATH%
    )
) else (
    echo  Already removed or never installed.
)
echo.
pause
exit /b 0
