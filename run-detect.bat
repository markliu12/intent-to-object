@echo off
rem Double-click to run the camera in "detect only" mode.
rem It opens the camera, prints what it recognises, and publishes NOTHING.
rem Press Esc (or close the window) to stop.

setlocal
cd /d "%~dp0"

if exist "%~dp0.venv\Scripts\ito.exe" (
    "%~dp0.venv\Scripts\ito.exe" run --show
) else (
    echo.
    echo No local environment found at ".venv".
    echo Set it up once with:
    echo     python -m venv .venv
    echo     .venv\Scripts\pip install -e ".[camera]"
    echo.
    echo Then double-click this file again.
    echo.
)

echo.
echo Stopped. Press any key to close this window.
pause >nul
