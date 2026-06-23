@echo off
cd /d "%~dp0.."
set "PATH=%~dp0..db\platform-tools;%PATH%"
python test_filter_real.py RS5XCI7XDYXWMFFI
pause
