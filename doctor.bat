@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

cd /d "%~dp0"
set "PATH=%~dp0adb\platform-tools;%PATH%"

echo.
echo ==========================================
echo   Automation Operations Doctor
echo ==========================================
echo.

set "ALL_OK=1"

:: ── Windows ──
echo [检查] Windows...
ver | find "10" >nul && echo   [OK] Windows 10
ver | find "11" >nul && echo   [OK] Windows 11

:: ── Python ──
echo [检查] Python...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo   [FAIL] Python 未安装
    set "ALL_OK=0"
) else (
    for /f "tokens=2" %%v in ('python --version 2^>^&1') do echo   [OK] Python %%v
)

:: ── Virtual Environment ──
echo [检查] 虚拟环境...
if exist ".venv\Scripts\activate.bat" (
    echo   [OK] .venv 存在
) else (
    echo   [WARN] .venv 不存在 (运行 install.bat 创建)
    set "ALL_OK=0"
)

:: ── Python Dependencies ──
echo [检查] Python 依赖...
if exist ".venv\Scripts\python.exe" (
    .venv\Scripts\python.exe -c "import uiautomator2; import cv2; import flask" >nul 2>&1
    if %errorlevel% equ 0 (
        echo   [OK] 核心依赖已安装
    ) else (
        echo   [WARN] 依赖不完整 (运行 install.bat)
        set "ALL_OK=0"
    )
) else (
    echo   [WARN] 无法检查 (虚拟环境不存在)
    set "ALL_OK=0"
)

:: ── ADB ──
echo [检查] ADB...
adb version >nul 2>&1
if %errorlevel% neq 0 (
    echo   [FAIL] ADB 不可用
    set "ALL_OK=0"
) else (
    for /f "tokens=5" %%v in ('adb version 2^>^&1 ^| findstr "version"') do echo   [OK] ADB %%v
)

:: ── ADB Server ──
echo [检查] ADB 服务...
adb start-server >nul 2>&1
echo   [OK] ADB 服务已启动

:: ── Device ──
echo [检查] 设备...
set "DEV_FOUND=0"
set "SERIAL="
for /f "tokens=1,2" %%a in ('adb devices 2^>nul ^| findstr /v "List of" ^| findstr /v "^$"') do (
    set /a DEV_FOUND+=1
    if "%%b"=="device" (
        echo   [OK] %%a - 已授权
        set "SERIAL=%%a"
    ) else if "%%b"=="unauthorized" (
        echo   [WARN] %%a - 未授权 (请看手机屏幕)
    ) else (
        echo   [WARN] %%a - %%b
    )
)
if %DEV_FOUND% equ 0 (
    echo   [WARN] 未检测到设备
    set "ALL_OK=0"
)

:: ── 设备信息 + 适配 ──
if defined SERIAL (
    echo.
    echo [设备信息]
    for /f "tokens=*" %%m in ('adb -s !SERIAL! shell getprop ro.product.manufacturer 2^>nul') do echo   制造商:   %%m
    for /f "tokens=*" %%m in ('adb -s !SERIAL! shell getprop ro.product.model 2^>nul') do echo   型号:     %%m
    for /f "tokens=*" %%m in ('adb -s !SERIAL! shell getprop ro.build.version.release 2^>nul') do echo   Android:  %%m
    for /f "tokens=*" %%m in ('adb -s !SERIAL! shell wm size 2^>nul') do echo   分辨率:   %%m
    for /f "tokens=*" %%m in ('adb -s !SERIAL! shell wm density 2^>nul') do echo   DPI:      %%m

    echo.
    echo [设备适配]
    if exist ".venv\Scripts\python.exe" (
        .venv\Scripts\python.exe -c "from device_profiles import DeviceProfileManager; d=DeviceProfileManager.resolve('!SERIAL!'); print(f'   屏幕: {d.width}x{d.height}'); print(f'   验证: {\"已实测\" if d.is_verified else \"自动适配\"}'); print(f'   置信度: {d.confidence:.0%}')" 2>nul
    ) else (
        echo   [WARN] 无法检查
    )
)

:: ── 项目配置 ──
echo.
echo [检查] 项目配置...
if exist ".env" (
    echo   [OK] .env 存在
) else if exist ".env.example" (
    echo   [OK] 已自动创建 .env
) else (
    echo   [WARN] 无配置文件
    set "ALL_OK=0"
)

:: ── 最终状态 ──
echo.
echo ==========================================
if %ALL_OK% equ 1 (
    echo   STATUS: READY
    echo   ^> 双击 start.bat 启动
) else (
    echo   STATUS: NOT READY
    echo   ^> 请先运行 install.bat
)
echo ==========================================
echo.

pause
