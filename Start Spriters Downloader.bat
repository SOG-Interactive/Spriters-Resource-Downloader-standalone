@echo off
setlocal
cd /d "%~dp0"
title Spriters Resource Downloader

if exist ".venv\Scripts\python.exe" goto :deps

echo First run: looking for Python 3.9 or newer...
set "PY="
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if not defined PY for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if exist "%%D\python.exe" set "PY=%%D\python.exe"
if not defined PY if exist "%USERPROFILE%\miniconda3\python.exe" set "PY=%USERPROFILE%\miniconda3\python.exe"
if not defined PY if exist "%USERPROFILE%\anaconda3\python.exe" set "PY=%USERPROFILE%\anaconda3\python.exe"
if not defined PY (
    python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1
    if not errorlevel 1 set "PY=python"
)
if not defined PY (
    echo.
    echo Python 3.9+ was not found.
    echo Install it from https://www.python.org/downloads/windows/ and tick "Add python.exe to PATH",
    echo then run this file again.
    pause
    exit /b 1
)
echo Using: %PY%
echo Creating the app's private Python environment (.venv)...
%PY% -m venv .venv
if errorlevel 1 (
    echo Could not create .venv
    pause
    exit /b 1
)

:deps
".venv\Scripts\python.exe" -c "import webview, bs4" >nul 2>&1
if errorlevel 1 (
    echo Installing dependencies...
    ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
    if errorlevel 1 (
        echo Dependency install failed. Check your internet connection and try again.
        pause
        exit /b 1
    )
)

echo Starting the app... (you can minimise this window)
".venv\Scripts\python.exe" -m srd_gui
if errorlevel 1 (
    echo.
    echo The app exited with an error. The messages above may explain why.
    pause
)
