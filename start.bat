@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
set "PATH=%~dp0adb\platform-tools;%PATH%"

if not exist ".venv\Scriptsctivate.bat" (
    echo [ERROR] venv not found. Run install.bat first.
    pause
    exit /b 1
)
call .venv\Scriptsctivate.bat

adb version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] ADB not available
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

echo [ERROR] No authorized device found!
echo   Run check-device.bat for details
pause
exit /b 1

:found
echo [OK] Device: %DEVICE%
echo.
echo [INFO] Starting (Ctrl+C to stop)...
echo.
python test_filter_real.py %DEVICE%
pause
