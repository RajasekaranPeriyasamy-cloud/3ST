@echo off
setlocal EnableExtensions
cd /d "%~dp0\.."

title 3ST API (port 8001)
color 0A

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo ERROR: Python venv not found at .venv\Scripts\python.exe
    echo Run Repair_Venv.cmd or:
    echo   python -m venv .venv
    echo   .venv\Scripts\pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

if not exist "data\logs" mkdir "data\logs"

set "LOCKFILE=data\logs\.api_running"

REM If API already answers, do not start a second copy (causes port/log fights).
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8001/health' -UseBasicParsing -TimeoutSec 2; exit ([int]($r.StatusCode -ne 200)) } catch { exit 1 }" >nul 2>&1
if %ERRORLEVEL%==0 (
    echo.
    echo API is ALREADY RUNNING at http://127.0.0.1:8001/health
    echo Keep the existing green API window open. Do not start a second copy.
    echo To restart: run Stop_3ST.cmd first, then Start_API.cmd once.
    echo.
    pause
    exit /b 0
)

if exist "%LOCKFILE%" (
    echo.
    echo Another API launcher is active, or a previous run did not clean up.
    echo 1. Close any other "3ST API" windows
    echo 2. Run Stop_3ST.cmd
    echo 3. Delete %LOCKFILE% if still stuck
    echo 4. Run Start_API.cmd ONCE
    echo.
    pause
    exit /b 1
)

echo %DATE% %TIME% > "%LOCKFILE%"

echo.
echo ========================================
echo   3ST FastAPI Engine
echo   http://127.0.0.1:8001/health
echo ========================================
echo.
echo KEEP THIS WINDOW OPEN while using the desk.
echo Run only ONE copy. Close extras and use Stop_3ST.cmd to restart.
echo.

call :ClearPort8001

:restart
echo [%date% %time%] Starting uvicorn ...
".venv\Scripts\python.exe" -m uvicorn api.main:app --host 127.0.0.1 --port 8001
set EXITCODE=%ERRORLEVEL%
echo.
echo API stopped (exit code %EXITCODE%).
if not %EXITCODE%==0 call :ClearPort8001
echo Restarting in 5 seconds ...
ping 127.0.0.1 -n 6 >nul
goto restart

:ClearPort8001
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8001" ^| findstr "LISTENING"') do (
    echo   Clearing PID %%a on port 8001
    taskkill /F /PID %%a >nul 2>&1
)
exit /b 0
