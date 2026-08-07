@echo off
REM Rebuild .venv after moving the repo (fixes OneDrive path in uvicorn launcher).
setlocal EnableExtensions
cd /d "%~dp0"

title 3ST Repair Python venv
color 0E

echo.
echo 3ST venv repair
echo ===============
echo.
echo Use this if you see:
echo   Fatal error in launcher ... OneDrive\Desktop\3ST\.venv
echo.
echo This recreates .venv at the current folder and reinstalls packages.
echo It may take several minutes.
echo.
choice /C YN /M "Continue"
if errorlevel 2 exit /b 0

where python >nul 2>&1
if errorlevel 1 (
    echo ERROR: python not found on PATH. Install Python 3.11+ first.
    pause
    exit /b 1
)

echo.
echo Stopping 3ST ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop_3st.ps1" 2>nul

if exist ".venv" (
    echo Renaming old .venv to .venv.bak ...
    if exist ".venv.bak" rmdir /s /q ".venv.bak" 2>nul
    ren ".venv" ".venv.bak"
)

echo Creating new venv ...
python -m venv .venv
if errorlevel 1 (
    echo ERROR: venv creation failed.
    pause
    exit /b 1
)

echo Installing requirements ...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: pip install failed.
    pause
    exit /b 1
)

echo.
echo Done. Test:
".venv\Scripts\python.exe" -m uvicorn --version
echo.
echo Now run Start_API.cmd once.
pause
