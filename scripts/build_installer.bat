@echo off
REM build_installer.bat — 用 Inno Setup 生成正式安装包
REM 产物: release\宝可梦自动化购买脚本_Setup_v%version%.exe (版本号读自 version.py)
chcp 65001 >nul
cd /d "%~dp0\.."

if not exist "dist\宝可梦自动化购买脚本\宝可梦自动化购买脚本.exe" (
    echo [ERROR] 未找到 Release 产物, 请先运行 build_release.bat
    exit /b 1
)

set "ISCC="
if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if exist "C:\Program Files\Inno Setup 6\ISCC.exe" set "ISCC=C:\Program Files\Inno Setup 6\ISCC.exe"
if not defined ISCC (
    echo [ERROR] 未找到 Inno Setup 6 ^(ISCC.exe^)
    echo 请先安装: https://jrsoftware.org/isdl.php
    exit /b 1
)

if not exist release mkdir release
echo ================================================
echo  生成安装包(Inno Setup)
echo ================================================
REM 版本号唯一源: version.py
REM 注意: 必须 /c: 字面量匹配 "APP_VERSION = ", 否则 /b "APP_VERSION"
REM 前缀会同时命中 APP_VERSION_TAG 行, 版本号被 f"v{APP_VERSION} 污染
for /f "usebackq tokens=2 delims== " %%v in (`findstr /b /c:"APP_VERSION = " version.py`) do set "VERSION=%%v"
set "VERSION=%VERSION:"=%"
echo  版本: %VERSION%
"%ISCC%" /DMyAppVersion=%VERSION% packaging\installer.iss
if errorlevel 1 (
    echo [FAILED] Inno Setup 构建失败
    exit /b 1
)
echo.
echo [OK] 安装包已生成到 release\ 目录
dir /b release\*.exe
exit /b 0
