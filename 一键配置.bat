@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ==========================================
echo   抖音自动化运营 — 一键配置
echo ==========================================
echo.

REM 1. Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到Python, 请先安装Python 3.9+
    echo 下载: https://www.python.org/downloads/
    pause
    exit /b 1
)
echo [OK] Python 已安装

REM 2. Install dependencies
echo [安装] pip依赖...
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple 2>&1
if errorlevel 1 (
    echo [警告] 部分依赖安装失败, 尝试默认源...
    pip install -r requirements.txt
)
echo [OK] 依赖安装完成

REM 3. Find ADB
set ADB_FOUND=0
if exist "%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe" (
    set ADB_FOUND=1
    echo [OK] ADB已找到: %LOCALAPPDATA%\Android\Sdk\platform-tools\
)
if exist "C:\platform-tools\adb.exe" (
    set ADB_FOUND=1
    echo [OK] ADB已找到: C:\platform-tools\
)

REM 4. Add ADB to PATH (current session)
if %ADB_FOUND%==1 (
    set "PATH=%LOCALAPPDATA%\Android\Sdk\platform-tools;%PATH%"
    echo [OK] ADB已加入PATH
) else (
    echo [提示] 未检测到ADB, 请手动安装Android SDK Platform Tools
    echo 下载: https://developer.android.com/studio/releases/platform-tools
)

REM 5. Init uiautomator2 on connected phones
echo [检查] 连接设备...
adb devices 2>nul | findstr "device$" >nul
if errorlevel 1 (
    echo [提示] 未检测到设备, 请连接手机并开启USB调试
) else (
    for /f "tokens=1" %%i in ('adb devices 2^>nul ^| findstr "device$"') do (
        echo [配置] %%i ...
        python -m uiautomator2 init -s %%i 2>nul
    )
)

REM 6. Create data dirs
mkdir data\screenshots 2>nul
mkdir data\logs 2>nul
mkdir data\models 2>nul

echo.
echo ==========================================
echo   配置完成! 双击 启动文件\ 下的bat启动
echo ==========================================
pause
