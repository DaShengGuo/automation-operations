@echo off
cd /d "%~dp0.."
set "PATH=%~dp0..\adb\platform-tools;%PATH%"
python test_filter_real.py AADE9X3A13W01108
pause
