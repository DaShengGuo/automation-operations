@echo off
REM build_release.bat — 生成正式 Release(客户版, 无控制台窗口)
REM 产物: dist\宝可梦自动化购买脚本\宝可梦自动化购买脚本.exe
chcp 65001 >nul
cd /d "%~dp0\.."

if not exist .venv-control\Scripts\python.exe (
    echo [ERROR] 未找到 .venv-control 虚拟环境
    exit /b 1
)

echo ================================================
echo  构建 Release: 宝可梦自动化购买脚本
echo ================================================
REM 从 version.py 生成 Windows 版本资源(唯一版本源)
.venv-control\Scripts\python.exe scripts\_gen_version_info.py
if errorlevel 1 (
    echo [FAILED] 版本资源生成失败
    exit /b 1
)
.venv-control\Scripts\python.exe -m PyInstaller packaging\pokemon_automation.spec --clean --noconfirm
if errorlevel 1 (
    echo [FAILED] PyInstaller 构建失败
    exit /b 1
)
echo.
echo [OK] Release 构建完成: dist\宝可梦自动化购买脚本\
exit /b 0
