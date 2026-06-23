@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "ADB_DIR=%~dp0adb"
echo ==========================================
echo   抖音自动化运营 — 一键配置
echo ==========================================
echo.

:: 1. Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [安装] Python 3.9...
    winget install Python.Python.3.9 --silent 2>nul
    if errorlevel 1 (
        echo [提示] 请手动安装Python: https://www.python.org/downloads/
        echo [提示] 安装时勾选 "Add Python to PATH"
        start https://www.python.org/downloads/
        pause
        exit /b 1
    )
    echo [OK] Python安装完成, 请重启终端后再次运行本脚本
    pause
    exit /b 0
)
echo [OK] Python

:: 2. ADB
where adb >nul 2>&1
if errorlevel 1 (
    if not exist "%ADB_DIR%db.exe" (
        echo [下载] ADB Platform Tools...
        mkdir "%ADB_DIR%" 2>nul
        powershell -Command "Invoke-WebRequest -Uri 'https://dl.google.com/android/repository/platform-tools-latest-windows.zip' -OutFile '%TEMP%db.zip'" 2>nul
        powershell -Command "Expand-Archive -Path '%TEMP%db.zip' -DestinationPath '%ADB_DIR%' -Force" 2>nul
        echo [OK] ADB下载完成
    )
    set "PATH=%ADB_DIR%\platform-tools;%PATH%"
)
adb version >nul 2>&1
if errorlevel 1 (
    echo [提示] ADB安装失败, 请手动下载
    echo https://developer.android.com/studio/releases/platform-tools
    pause
    exit /b 1
)
echo [OK] ADB

:: 3. Python依赖
echo [安装] pip依赖...
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple 2>nul
if errorlevel 1 pip install -r requirements.txt
echo [OK] 依赖

:: 4. 目录
mkdir data\screenshots 2>nul
mkdir data\logs 2>nul
mkdir data\models 2>nul

:: 5. 初始化手机
echo [检查] 连接设备...
for /f "tokens=1" %%i in ('adb devices 2^>nul ^| findstr "device$"') do (
    echo [配置] %%i
    python -m uiautomator2 init -s %%i 2>nul
)

echo.
echo ==========================================
echo   配置完成! 双击 启动文件\ 下bat启动
echo ==========================================
pause
