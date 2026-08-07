# Morning dev workflow: API (8001) + Vite UI (8080). Opens browser when both are ready.
# Default: NO --reload (stable for live trading). OneDrive + scheduler JSON writes cause reload loops.
param(
    [switch]$HotReload,
    [switch]$ForceRestart
)

$ErrorActionPreference = "Stop"
$ApiPort = 8001
$UiPort = 8080
$Root = Split-Path $PSScriptRoot -Parent
$UiRoot = Join-Path $Root "Pixel Perfect UI"
$UiUrl = "http://127.0.0.1:$UiPort"
$ApiUrl = "http://127.0.0.1:$ApiPort"

function Stop-PortListener {
    param([int]$LocalPort)
    Get-NetTCPConnection -LocalPort $LocalPort -State Listen -ErrorAction SilentlyContinue |
        ForEach-Object { taskkill /F /PID $_.OwningProcess /T 2>$null }
}

function Wait-HttpReady {
    param(
        [string]$Url,
        [int]$TimeoutSec = 60
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
            if ($r.StatusCode -eq 200) { return $true }
        } catch {
            Start-Sleep -Seconds 1
        }
    }
    return $false
}

function Test-ApiHealthy {
    try {
        $r = Invoke-WebRequest -Uri "$ApiUrl/health" -UseBasicParsing -TimeoutSec 2
        return $r.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Test-UiHealthy {
    try {
        $r = Invoke-WebRequest -Uri $UiUrl -UseBasicParsing -TimeoutSec 2
        return $r.StatusCode -eq 200
    } catch {
        return $false
    }
}

$apiAlreadyUp = (-not $ForceRestart) -and (Test-ApiHealthy)
$uiAlreadyUp = (-not $ForceRestart) -and (Test-UiHealthy)

if ($ForceRestart) {
    foreach ($p in @(8001, 8080, 8081, 8082, 5173)) {
        Stop-PortListener -LocalPort $p
    }
    Start-Sleep -Seconds 1
    $apiAlreadyUp = $false
    $uiAlreadyUp = $false
} elseif ($apiAlreadyUp) {
    Write-Host "API already healthy on $ApiUrl - leaving it running (use -ForceRestart to replace)."
    if (-not $uiAlreadyUp) {
        foreach ($p in @(8080, 8081, 8082, 5173)) {
            Stop-PortListener -LocalPort $p
        }
        Start-Sleep -Seconds 1
    }
} else {
    foreach ($p in @(8001, 8080, 8081, 8082, 5173)) {
        Stop-PortListener -LocalPort $p
    }
    Start-Sleep -Seconds 1
}

$python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Error "Python venv not found. Run Repair_Venv.cmd or: python -m venv .venv; pip install -r requirements.txt"
}

$envDev = Join-Path $UiRoot ".env.development"
if (-not (Test-Path $envDev)) {
    $envBody = @(
        "# Vite dev server talks to FastAPI on 8001"
        "VITE_API_BASE_URL=http://127.0.0.1:8001"
    ) -join [Environment]::NewLine
    Set-Content -Path $envDev -Value $envBody -Encoding utf8
    Write-Host "Created $envDev"
}

if (-not $apiAlreadyUp) {
    Write-Host "Starting API on $ApiUrl (batch launcher, auto-restarts on crash) ..."
    $apiBat = Join-Path $Root "scripts\start_api.bat"
    Start-Process cmd.exe -ArgumentList @("/k", "`"$apiBat`"") | Out-Null
} else {
    Write-Host "Skipping API start."
}

if (-not $uiAlreadyUp) {
    Write-Host "Starting UI on $UiUrl ..."
    Start-Process powershell -ArgumentList @(
        "-NoExit", "-Command",
        "Set-Location '$UiRoot'; npm run dev"
    ) | Out-Null
} else {
    Write-Host "UI already running on $UiUrl - skipping UI start."
}

Start-Sleep -Seconds 3

Write-Host "Waiting for API (may take up to 2 min on first boot) ..."
if (-not (Wait-HttpReady "$ApiUrl/health" 120)) {
    Write-Warning "API did not respond within 120s. Open the green '3ST API' cmd window for errors, or double-click Start_API.cmd."
} else {
    Write-Host "API ready."
}

Write-Host "Waiting for UI ..."
if (-not (Wait-HttpReady $UiUrl 120)) {
    Write-Warning "UI did not respond on $UiUrl. Check the UI window for errors."
} else {
    Write-Host "UI ready."
}

cmd /c start "" $UiUrl
Write-Host ""
Write-Host "3ST is up."
Write-Host "  UI:  $UiUrl"
Write-Host "  API: $ApiUrl"
Write-Host "  Login: $UiUrl/login"
Write-Host ""
Write-Host "Bookmark the UI URL above. Stop with: .\scripts\stop_3st.ps1"
