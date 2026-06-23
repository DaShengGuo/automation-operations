@echo off
cd /d "%~dp0.."
set "PATH=%~dp0..db\platform-tools;%PATH%"
python test_filter_real.py 89U899UOYPAQXSCI
pause
