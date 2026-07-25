# Keeps the 3ST FastAPI process alive - restarts on crash (quant-desk style).
param(
    [int]$Port = 8001,
    [int]$RestartDelaySec = 3
)

$ErrorActionPreference = "Continue"
$Root = Split-Path $PSScriptRoot -Parent
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$LogDir = Join-Path $Root "data\logs"
$LogFile = Join-Path $LogDir "api_supervisor.log"

if (-not (Test-Path $Python)) {
    Write-Error "Python venv not found at $Python"
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
Set-Location $Root

function Write-Log {
    param([string]$Message)
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $Message"
    Add-Content -Path $LogFile -Value $line -Encoding utf8
    Write-Host $line
}

function Test-ApiHealthy {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/health" -UseBasicParsing -TimeoutSec 2
        return $r.StatusCode -eq 200
    } catch {
        return $false
    }
}

Write-Log "Supervisor started (port $Port). Ctrl+C to stop."

while ($true) {
    if (Test-ApiHealthy) {
        Write-Log "API already healthy on port $Port - supervisor idle (use stop_3st.ps1 to kill)."
        while (Test-ApiHealthy) {
            Start-Sleep -Seconds 5
        }
        Write-Log "API stopped responding - will restart."
    }

    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        ForEach-Object {
            Write-Log "Clearing stale listener PID $($_.OwningProcess) on port $Port"
            taskkill /F /PID $_.OwningProcess /T 2>$null
        }

    Write-Log "Starting uvicorn on 127.0.0.1:$Port ..."
    $proc = Start-Process -FilePath $Python -ArgumentList @(
        "-m", "uvicorn", "api.main:app", "--host", "127.0.0.1", "--port", "$Port"
    ) -PassThru -NoNewWindow -Wait

    $code = $proc.ExitCode
    Write-Log "uvicorn exited with code $code - restarting in ${RestartDelaySec}s"
    Start-Sleep -Seconds $RestartDelaySec
}
