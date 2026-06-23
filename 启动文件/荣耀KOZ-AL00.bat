@echo off
cd /d "%~dp0.."
set "PATH=%~dp0..db\platform-tools;%PATH%"
python auto_launch.py honor
pause
