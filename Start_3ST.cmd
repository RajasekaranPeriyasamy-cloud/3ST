@echo off
REM Launch 3ST without changing system ExecutionPolicy (Bypass for this process only).
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_3st_dev.ps1"
pause
