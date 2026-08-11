@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
set "PATH=%~dp0adb\platform-tools;%PATH%"

echo.
echo ==========================================
echo   Douyin Automation - Start
echo ==========================================
echo.

if exist ".venv\Scripts\python.exe" (
    set "PYTHON=.venv\Scripts\python.exe"
    echo [OK] Using venv Python
) else (
    python --version >nul 2>&1
    if %errorlevel% neq 0 (
        echo [ERROR] Python not found
        pause
        exit /b 1
    )
    set "PYTHON=python"
    echo [OK] Using system Python
)

adb version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] ADB not found
    pause
    exit /b 1
)
adb start-server >nul 2>&1

echo [INFO] Detecting device...
set "DEVICE="
for /f "tokens=1" %%d in ('adb devices 2^>nul ^| findstr "device$"') do (
    set "DEVICE=%%d"
    goto :found
)

echo [ERROR] No device found!
pause
exit /b 1

:found
echo [OK] Device: %DEVICE%
echo.
echo [INFO] Starting (Ctrl+C to stop)...
echo.
%PYTHON% test_filter_real.py %DEVICE%
pause
