@echo off
REM build_debug.bat — 生成 Debug 版(带控制台, 排查客户问题用)
REM 产物: dist\宝可梦自动化购买脚本_debug\
chcp 65001 >nul
cd /d "%~dp0\.."

if not exist .venv-control\Scripts\python.exe (
    echo [ERROR] 未找到 .venv-control 虚拟环境
    exit /b 1
)

echo ================================================
echo  构建 Debug 版(带控制台输出)
echo ================================================
.venv-control\Scripts\python.exe -c "import shutil; shutil.copy('packaging/pokemon_automation.spec', 'packaging/_debug.spec')"
REM 替换: console=False → console=True; 产物目录加 _debug 后缀
powershell -NoProfile -Command ^
  "$s = Get-Content -Raw 'packaging/_debug.spec';" ^
  "$s = $s.Replace('console=False', 'console=True');" ^
  "$s = $s.Replace('name=ver.APP_NAME,', 'name=ver.APP_NAME + ''_debug'',');" ^
  "Set-Content -NoNewline 'packaging/_debug.spec' $s"
.venv-control\Scripts\python.exe -m PyInstaller packaging\_debug.spec --clean --noconfirm
if errorlevel 1 (
    echo [FAILED] Debug 构建失败
    exit /b 1
)
del packaging\_debug.spec 2>nul
echo.
echo [OK] Debug 构建完成: dist\宝可梦自动化购买脚本_debug\
exit /b 0
