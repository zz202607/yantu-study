@echo off
cd /d "%~dp0"
py -3 tools\launch.py --stop
if errorlevel 1 pause
