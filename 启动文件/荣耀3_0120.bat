@echo off
cd /d "%~dp0.."
set "PATH=%~dp0..db\platform-tools;%PATH%"
python test_filter_real.py AADE9X3A20W00120
pause
