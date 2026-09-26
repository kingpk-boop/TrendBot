@echo off
setlocal
rem TrendBot launcher for Windows: double-click this file.
rem Extra options are passed on, e.g.  start.bat --host 0.0.0.0
cd /d "%~dp0"
title TrendBot

rem --- find Python ("py" launcher first; plain "python" may be the Microsoft Store stub)
set "PY="
py -3 --version >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if not defined PY (
    python --version >nul 2>&1
    if not errorlevel 1 set "PY=python"
)
if not defined PY (
    echo.
    echo Python 3 was not found.
    echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH",
    echo then double-click start.bat again.
    echo.
    pause
    exit /b 1
)

rem --- private environment in .venv, so nothing is installed system-wide
if not exist ".venv\Scripts\python.exe" (
    echo Creating a private Python environment in .venv - first run only...
    %PY% -m venv .venv
    if errorlevel 1 goto venv_failed
)
set "VPY=.venv\Scripts\python.exe"

rem --- install packages only when requirements.txt has changed since the last install
fc /b requirements.txt .venv\requirements.installed >nul 2>&1
if errorlevel 1 (
    echo Installing packages - this takes a minute the first time...
    "%VPY%" -m pip install --disable-pip-version-check -q --upgrade pip
    "%VPY%" -m pip install --disable-pip-version-check -r requirements.txt
    if errorlevel 1 goto pip_failed
    copy /y requirements.txt .venv\requirements.installed >nul
)

"%VPY%" run.py %*
echo.
echo TrendBot has stopped.
pause
exit /b 0

:venv_failed
echo.
echo Could not create the Python environment. Try deleting the .venv folder and running again.
pause
exit /b 1

:pip_failed
echo.
echo Installing packages failed - check your internet connection and try again.
echo If it keeps failing, delete the .venv folder and run start.bat again.
pause
exit /b 1
