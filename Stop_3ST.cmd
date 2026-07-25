@echo off
REM Stop 3ST API and UI (ports 8001, 8080, etc.)
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop_3st.ps1"
pause
