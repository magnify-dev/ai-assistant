@echo off
setlocal
cd /d "%~dp0voice"
start "Jarvis Control Panel" ".\.venv\Scripts\pythonw.exe" "control_panel.py"
