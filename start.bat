@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
set "PATH=%~dp0adb\platform-tools;%PATH%"

echo.
echo ==========================================
echo   Douyin Automation — Start
echo ==========================================
echo.

:: Check virtual environment
if not exist ".venv\Scripts\activate.bat" (
    echo [ERROR] venv not found. Run install.bat first.
    pause
    exit /b 1
)
call .venv\Scripts\activate.bat

:: Check ADB
adb version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] ADB not available
    pause
    exit /b 1
)

:: Start ADB server
adb start-server >nul 2>&1

:: Detect device
echo [INFO] Detecting device...
set "DEVICE="
for /f "tokens=1" %%d in ('adb devices 2^>nul ^| findstr "device$"') do (
    set "DEVICE=%%d"
    goto :found
)

echo [ERROR] No authorized device found!
echo   - Check USB cable supports data transfer
echo   - Check USB debugging is ON
echo   - Check phone screen for auth dialog
echo   Run check-device.bat for details
pause
exit /b 1

:found
echo [OK] Device: %DEVICE%

:: Run
echo.
echo [INFO] Starting automation...
echo Press Ctrl+C to stop
echo.
python test_filter_real.py %DEVICE%

pause
