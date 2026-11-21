@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
py -3 tools\wechat_watch.py %*
if errorlevel 1 pause
