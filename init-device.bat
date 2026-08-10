@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PATH=%~dp0adb\platform-tools;%PATH%"

echo.
echo ==========================================
echo   uiautomator2 设备初始化
echo ==========================================
echo.

:: ── 1. 检查 ADB ──
adb version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] ADB 不可用，请先运行 install.bat
    pause
    exit /b 1
)

:: ── 2. 检查设备 ──
echo [INFO] 检测设备...
adb start-server >nul 2>&1

set "DEVICE_COUNT=0"
for /f "tokens=1,2" %%a in ('adb devices 2^>nul ^| findstr /v "List of" ^| findstr /v "^$"') do (
    set /a DEVICE_COUNT+=1
    set "STATUS=%%b"
    set "SERIAL=%%a"
)

if %DEVICE_COUNT% equ 0 (
    echo [ERROR] 未检测到设备
    echo   请先连接手机并授权 USB 调试
    echo   运行 check-device.bat 查看详情
    pause
    exit /b 1
)

echo   [OK] 设备: %SERIAL% (%STATUS%)

:: ── 3. 检查虚拟环境 ──
if not exist ".venv\Scripts\activate.bat" (
    echo [ERROR] 虚拟环境未安装，请先运行 install.bat
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat

:: ── 4. 初始化 uiautomator2 ──
echo [INFO] 初始化 uiautomator2 (安装 ATX 代理到手机)...
echo   这可能需要 1-2 分钟...
echo.

python -m uiautomator2 init -s %SERIAL%

if %errorlevel% equ 0 (
    echo.
    echo [OK] uiautomator2 初始化成功！
    echo   现在可以运行 start.bat 启动自动化
) else (
    echo.
    echo [ERROR] 初始化失败
    echo   请检查:
    echo   - 手机是否已授权 USB 调试
    echo   - 手机屏幕是否解锁
    echo   - 网络是否正常
)

pause
