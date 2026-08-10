@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PATH=%~dp0adb\platform-tools;%PATH%"

echo.
echo ==========================================
echo   抖音自动化运营 — 设备检测
echo ==========================================
echo.

:: ── 1. 检查 ADB ──
adb version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] ADB 不可用
    echo   请先运行 install.bat
    pause
    exit /b 1
)
echo [OK] ADB 可用

:: ── 2. 重启 ADB ──
echo [INFO] 重启 ADB 服务...
adb kill-server >nul 2>&1
timeout /t 1 /nobreak >nul
adb start-server >nul 2>&1
timeout /t 2 /nobreak >nul

:: ── 3. 检测设备 ──
echo.
echo [INFO] 扫描设备...
echo ==========================================
adb devices -l
echo ==========================================
echo.

:: ── 4. 分析设备状态 ──
set "DEVICE_COUNT=0"
set "AUTHORIZED=0"
set "UNAUTHORIZED=0"
set "OFFLINE=0"

for /f "tokens=1,2" %%a in ('adb devices 2^>nul ^| findstr /v "List of" ^| findstr /v "^$"') do (
    set /a DEVICE_COUNT+=1
    if "%%b"=="device" (
        set /a AUTHORIZED+=1
        set "DEVICE_SERIAL=%%a"
        echo [OK] %%a 已授权，可以使用
    ) else if "%%b"=="unauthorized" (
        set /a UNAUTHORIZED+=1
        echo [WARN] %%a 未授权
    ) else if "%%b"=="offline" (
        set /a OFFLINE+=1
        echo [WARN] %%a 已离线
    )
)

echo.

:: ── 5. 诊断提示 ──
if %DEVICE_COUNT% equ 0 (
    echo ╔══════════════════════════════════╗
    echo ║  【未检测到任何设备】           ║
    echo ╚══════════════════════════════════╝
    echo.
    echo 请依次检查:
    echo   1. USB 数据线是否支持"数据传输"？ (不是纯充电线)
    echo   2. 手机 设置 ^> 开发者选项 ^> USB 调试 是否打开？
    echo   3. 重新插拔 USB 线
    echo   4. 手机解锁看屏幕，是否弹出"允许 USB 调试"对话框？
    echo   5. 详细教程: docs/PHONE_SETUP.md
    echo.
    goto :end
)

if %UNAUTHORIZED% gtr 0 (
    echo ╔══════════════════════════════════╗
    echo ║  【USB 调试未授权！】           ║
    echo ╚══════════════════════════════════╝
    echo.
    echo 请查看手机屏幕:
    echo   弹出 "是否允许使用此计算机进行 USB 调试？"
    echo   → 勾选 "始终允许使用这台计算机"
    echo   → 点击 "允许"
    echo.
    echo 如果未弹出:
    echo   → 手机 设置 ^> 开发者选项 ^> 撤销 USB 调试授权
    echo   → 重新插拔 USB 线
    echo   → 再次授权
    echo.
    goto :end
)

if %OFFLINE% gtr 0 (
    echo ╔══════════════════════════════════╗
    echo ║  【设备已离线】                 ║
    echo ╚══════════════════════════════════╝
    echo.
    echo 请尝试:
    echo   1. 重新插拔 USB 线
    echo   2. 关闭再开启 USB 调试
    echo   3. 撤销 USB 调试授权后重新授权
    echo.
    goto :end
)

if %AUTHORIZED% gtr 1 (
    echo [INFO] 检测到多台设备 (%AUTHORIZED% 台)
    echo   start.bat 将自动使用第一台: %DEVICE_SERIAL%
    echo.
)

echo ╔══════════════════════════════════╗
echo ║  【设备就绪，可以启动】          ║
echo ╚══════════════════════════════════╝
echo.
echo   双击 start.bat 开始自动化
echo.

:end
pause
