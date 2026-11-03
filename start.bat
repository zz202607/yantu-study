@echo off
cd /d "%~dp0"
py -3 tools\launch.py %*
if errorlevel 1 pause
