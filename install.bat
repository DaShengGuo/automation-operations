@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

cd /d "%~dp0"

echo.
echo ==========================================
echo   抖音自动化运营 — 一键安装
echo ==========================================
echo.

set "STEP=0"
set "TOTAL=6"

:: ── [1/6] 检查 Python ──
set /a STEP+=1
echo [!STEP!/%TOTAL%] 检查 Python...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [WARN] 未检测到 Python，尝试自动安装...
    where winget >nul 2>&1
    if %errorlevel% equ 0 (
        echo   正在通过 winget 安装 Python 3.10...
        winget install Python.Python.3.10 --accept-package-agreements --accept-source-agreements --silent 2>nul
        if %errorlevel% neq 0 (
            winget install Python.Python.3.11 --accept-package-agreements --accept-source-agreements --silent 2>nul
        )
        if %errorlevel% neq 0 (
            winget install Python.Python.3.12 --accept-package-agreements --accept-source-agreements --silent 2>nul
        )
        echo [INFO] Python 安装完成。请关闭此窗口，重新打开后再次运行 install.bat
        pause
        exit /b 0
    ) else (
        echo [ERROR] 未找到 winget，无法自动安装 Python
        echo   请手动下载安装: https://www.python.org/downloads/
        echo   安装时勾选 "Add Python to PATH"
        pause
        exit /b 1
    )
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set "PYVER=%%v"
echo   [OK] Python %PYVER%

:: ── [2/6] 创建虚拟环境 ──
set /a STEP+=1
echo [!STEP!/%TOTAL%] 创建虚拟环境...
if not exist ".venv" (
    python -m venv .venv
    if %errorlevel% neq 0 (
        echo [ERROR] 虚拟环境创建失败
        pause
        exit /b 1
    )
    echo   [OK] 虚拟环境已创建
) else (
    echo   [OK] 虚拟环境已存在，跳过
)

:: ── [3/6] 安装 Python 依赖 ──
set /a STEP+=1
echo [!STEP!/%TOTAL%] 安装 Python 依赖...
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip -q 2>nul
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple 2>nul
if %errorlevel% neq 0 (
    echo   [INFO] 清华镜像失败，尝试官方源...
    pip install -r requirements.txt 2>nul
)
if %errorlevel% neq 0 (
    echo [ERROR] 依赖安装失败，请检查网络连接
    pause
    exit /b 1
)
echo   [OK] Python 依赖已安装

:: ── [4/6] 检查 ADB ──
set /a STEP+=1
echo [!STEP!/%TOTAL%] 检查 ADB...
if exist "adb\platform-tools\adb.exe" (
    echo   [OK] ADB 已包含在项目中
) else (
    echo   [INFO] ADB 未找到，将使用系统 PATH 中的 adb
)
:: 确保 ADB 可执行
set "PATH=%~dp0adb\platform-tools;%PATH%"
adb version >nul 2>&1
if %errorlevel% neq 0 (
    echo [WARN] ADB 不可用，但可能不影响使用
    echo   请确保手机正确连接并安装了驱动
) else (
    echo   [OK] ADB 可用
)

:: ── [5/6] 创建运行时目录 ──
set /a STEP+=1
echo [!STEP!/%TOTAL%] 创建运行时目录...
if not exist "data\screenshots" mkdir "data\screenshots"
if not exist "data\logs"        mkdir "data\logs"
if not exist "data\models"      mkdir "data\models"
echo   [OK] 目录已就绪

:: ── [6/6] 初始化环境配置 ──
set /a STEP+=1
echo [!STEP!/%TOTAL%] 初始化配置...
if not exist ".env" (
    copy .env.example .env >nul 2>&1
    echo   [OK] 已创建 .env (从 .env.example 复制)
) else (
    echo   [OK] .env 已存在，跳过
)

:: ── 完成 ──
echo.
echo ==========================================
echo   安装完成！
echo ==========================================
echo.
echo   接下来:
echo   1. 根据 docs/PHONE_SETUP.md 设置手机
echo   2. USB 连接手机
echo   3. 双击 check-device.bat 检测设备
echo   4. 双击 start.bat 启动
echo.
pause
