@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PATH=%~dp0adb\platform-tools;%PATH%"
echo ==========================================
echo   抖音自动化运营 — 一键配置
echo ==========================================
echo.
echo [OK] ADB (项目自带)

:: Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [安装] Python...
    winget install Python.Python.3.9 --silent 2>nul
    if errorlevel 1 (
        echo 请手动安装Python并勾选"Add to PATH"
        start https://www.python.org/downloads/
        pause & exit /b 1
    )
    echo [OK] 安装完成, 请重启终端再次运行
    pause & exit /b 0
)
echo [OK] Python

:: Dependencies
echo [安装] pip依赖...
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple 2>nul
if errorlevel 1 pip install -r requirements.txt
echo [OK] 依赖

:: Dirs
mkdir data\screenshots 2>nul
mkdir data\logs 2>nul
mkdir data\models 2>nul

:: Init phones
echo [配置] 手机...
for /f "tokens=1" %%i in ('adb devices 2^>nul ^| findstr "device$"') do (
    echo   %%i ...
    python -m uiautomator2 init -s %%i 2>nul
)

echo.
echo ==========================================
echo   完成! 双击 启动文件 下bat启动
echo ==========================================
pause
