@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo   抖音自动化评论运营系统
echo   设备: MuMu模拟器 (127.0.0.1:7555)
echo   Dashboard: http://localhost:5800
echo ========================================
echo.
echo 按 Ctrl+C 停止运行
echo.

python -m comment_bot.main
pause
