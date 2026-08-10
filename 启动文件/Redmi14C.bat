@echo off
cd /d "%~dp0.."
set "PATH=%~dp0..\adb\platform-tools;%PATH%"
python auto_launch.py redmi
pause
