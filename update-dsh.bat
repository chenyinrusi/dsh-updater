@echo off
setlocal
cd /d "%~dp0"
title DeepSeek Harness Updater

rem ---------------------------------------------------------------
rem  DeepSeek Harness updater (double-click to run)
rem  Auto-elevates to Administrator when needed (pnpm install on
rem  Windows needs symlink privileges). No elevation for --check.
rem ---------------------------------------------------------------

set "ELEVATE=1"
set "PYARGS="
:parse
if "%~1"=="" goto done_parse
if /i "%~1"=="--no-elevate" (set "ELEVATE=0") else (set "PYARGS=%PYARGS% %~1")
shift
goto parse
:done_parse

rem --check is read-only, no elevation needed
echo %PYARGS% | findstr /i /c:"--check" >nul 2>&1
if not errorlevel 1 set "ELEVATE=0"

if "%ELEVATE%"=="1" (
    net session >nul 2>&1
    if errorlevel 1 (
        echo Requesting administrator privileges - needed for pnpm symlinks...
        powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '/no-elevate%PYARGS%' -Verb RunAs"
        exit /b 0
    )
)

set "PY="
where python >nul 2>&1
if not errorlevel 1 (
    set "PY=python"
) else (
    where py >nul 2>&1
    if not errorlevel 1 set "PY=py -3"
)
if "%PY%"=="" (
    echo [ERROR] Python not found. Install Python 3.9+ from https://www.python.org
    pause
    exit /b 1
)

%PY% update-dsh.py %PYARGS%
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (
    echo [OK] Done. Full log: %~dp0update-dsh.log
) else (
    echo [FAILED] Exit code %RC%. Full log: %~dp0update-dsh.log
)
pause
exit /b %RC%
