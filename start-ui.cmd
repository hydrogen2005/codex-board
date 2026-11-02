@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 goto use_python
py -3 codex_usage_ui.py %*
goto finished
:use_python
python codex_usage_ui.py %*
:finished
if errorlevel 1 pause
