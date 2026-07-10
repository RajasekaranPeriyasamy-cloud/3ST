# Clean start for 3ST FastAPI (fixes stale /auth/login on port 8000)
$Port = 8000
$Root = Split-Path $PSScriptRoot -Parent

Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { taskkill /F /PID $_.OwningProcess /T 2>$null }

Start-Sleep -Seconds 2

Remove-Item -Recurse -Force "$Root\api\__pycache__" -ErrorAction SilentlyContinue

Set-Location $Root
& "$Root\.venv\Scripts\uvicorn.exe" api.main:app --host 127.0.0.1 --port $Port
