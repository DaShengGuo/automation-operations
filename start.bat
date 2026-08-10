@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

cd /d "%~dp0"
set "PATH=%~dp0adb\platform-tools;%PATH%"

echo.
echo ==========================================
echo   抖音自动化运营 — 启动
echo ==========================================
echo.

:: ── 1. 检查虚拟环境 ──
if not exist ".venv\Scripts\activate.bat" (
    echo [ERROR] 虚拟环境未安装
    echo   请先运行 install.bat
    pause
    exit /b 1
)

:: ── 2. 激活虚拟环境 ──
call .venv\Scripts\activate.bat

:: ── 3. 检查 ADB ──
adb version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] ADB 不可用
    pause
    exit /b 1
)

:: ── 4. 启动 ADB server ──
adb start-server >nul 2>&1

:: ── 5. 检查设备 ──
echo [INFO] 检测设备...
for /f "tokens=1" %%d in ('adb devices 2^>nul ^| findstr "device$"') do (
    set "DEVICE=%%d"
    goto :device_found
)

:: 未找到设备
echo [ERROR] 未检测到已授权的设备!
echo.
echo   请检查:
echo   - USB 数据线是否支持数据传输
echo   - 手机是否开启 USB 调试
echo   - 手机屏幕是否弹出授权对话框
echo.
echo   运行 check-device.bat 查看详细信息
pause
exit /b 1

:device_found
echo   [OK] 设备: %DEVICE%

:: ── 6. 设置环境变量 ──
set "DOUYIN_DEVICE=%DEVICE%"

:: ── 7. 启动主程序 ──
echo.
echo [INFO] 启动自动化脚本...
echo   按 Ctrl+C 停止
echo.
python test_filter_real.py %DEVICE%

:: ── 结束 ──
pause
