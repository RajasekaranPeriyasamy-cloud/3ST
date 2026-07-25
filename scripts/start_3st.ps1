# Daily trading mode: single process on http://127.0.0.1:8001 (API + built UI).
param(
    [switch]$RebuildUi
)

$ErrorActionPreference = "Stop"
$Port = 8001
$Root = Split-Path $PSScriptRoot -Parent
$UiRoot = Join-Path $Root "Pixel Perfect UI"
$python = Join-Path $Root ".venv\Scripts\python.exe"

Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { taskkill /F /PID $_.OwningProcess /T 2>$null }
Start-Sleep -Seconds 1

if ($RebuildUi) {
    Write-Host "Building UI..."
    Push-Location $UiRoot
    npm run build
    if ($LASTEXITCODE -ne 0) { Pop-Location; exit $LASTEXITCODE }
    Pop-Location
} elseif (-not (Test-Path "$UiRoot\.output\public\index.html")) {
    Write-Host "UI build missing - running npm run build once..."
    Push-Location $UiRoot
    npm run build
    if ($LASTEXITCODE -ne 0) { Pop-Location; exit $LASTEXITCODE }
    Pop-Location
}

if (-not (Test-Path $python)) {
    Write-Error "Python venv not found at $python"
}

Write-Host "Starting 3ST on http://127.0.0.1:$Port ..."
Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "Set-Location '$Root'; & '$python' -m uvicorn api.main:app --host 127.0.0.1 --port $Port"
) | Out-Null

$deadline = (Get-Date).AddSeconds(30)
while ((Get-Date) -lt $deadline) {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/health" -UseBasicParsing -TimeoutSec 2
        if ($r.StatusCode -eq 200) { break }
    } catch {
        Start-Sleep -Seconds 1
    }
}

Start-Process "http://127.0.0.1:$Port"
Write-Host "Opened http://127.0.0.1:$Port"
