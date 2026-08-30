# Stop 3ST API and Vite dev servers.
#
# Order matters. The API launchers are RESTART LOOPS -- scripts\start_api.bat has
# a :restart label, run_api_supervisor.ps1 a while($true). Killing only the port
# listener leaves the launcher alive and it respawns uvicorn ~5s later, so the
# documented stop -> npm run build -> start sequence ends up with TWO launchers
# fighting over 8001: each one's ClearPort8001 kills the other's healthy uvicorn,
# and you get four uvicorn copies, each with its own in-memory ARM state and risk
# counters (see CLAUDE.md, "Runtime Constraints"). So: launchers first, then
# whatever is still holding a port.

$ErrorActionPreference = "Continue"
$Root = Split-Path $PSScriptRoot -Parent
$ports = @(8001, 8080, 8081, 8082, 5173)

# Never kill this process or any process it is running inside. stop_3st.ps1 is
# normally invoked from Stop_3ST.cmd, and may be run by hand from a window that a
# launcher script opened -- without this it would kill its own host mid-run.
$selfChain = New-Object System.Collections.Generic.HashSet[int]
$walk = $PID
for ($i = 0; $i -lt 12 -and $walk; $i++) {
    [void]$selfChain.Add($walk)
    $parent = (Get-CimInstance Win32_Process -Filter "ProcessId=$walk" -ErrorAction SilentlyContinue).ParentProcessId
    if (-not $parent -or $parent -eq $walk) { break }
    $walk = $parent
}

# 3ST-specific launcher entry points. Names are distinctive enough to match on
# their own; `npm run dev` is not, so it additionally has to mention $Root.
$launcherPatterns = @(
    'start_api\.bat',
    'Start_API\.cmd',
    'Run_API\.cmd',
    'start_api\.ps1',
    'run_api_supervisor\.ps1',
    'start_3st_dev\.ps1',
    'start_3st\.ps1',
    'Start_3ST\.cmd'
)
$launcherRegex = '(?i)(' + ($launcherPatterns -join '|') + ')'
$rootEscaped = [regex]::Escape($Root)
$uiDevRegex = "(?i)npm(\.cmd)?\s+run\s+dev"

$killed = 0

function Stop-Pid {
    param([int]$ProcessId, [string]$Why)
    if ($selfChain.Contains($ProcessId)) { return $false }
    Write-Host "Stopping PID $ProcessId ($Why)"
    taskkill /F /PID $ProcessId /T 2>$null | Out-Null
    return $true
}

# --- 1. Launcher / supervisor windows -------------------------------------
$procs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -in @('cmd.exe', 'powershell.exe', 'pwsh.exe') -and $_.CommandLine }

foreach ($p in $procs) {
    $cl = $p.CommandLine
    $isLauncher = $cl -match $launcherRegex
    $isUiDev = ($cl -match $uiDevRegex) -and ($cl -match $rootEscaped)
    if (-not ($isLauncher -or $isUiDev)) { continue }
    # A stop_* script mentions no launcher name, but guard anyway.
    if ($cl -match '(?i)stop_3st') { continue }
    $why = if ($isLauncher) { "launcher" } else { "UI dev server" }
    if (Stop-Pid -ProcessId $p.ProcessId -Why $why) { $killed++ }
}

if ($killed -gt 0) {
    # Give a half-killed restart loop time to actually exit before we look at ports.
    Start-Sleep -Seconds 2
}

# --- 2. Anything still listening ------------------------------------------
foreach ($port in $ports) {
    Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        ForEach-Object {
            if (Stop-Pid -ProcessId $_.OwningProcess -Why "port $port") { $killed++ }
        }
}

Start-Sleep -Seconds 1
Remove-Item (Join-Path $Root "data\logs\.api_running") -Force -ErrorAction SilentlyContinue

# --- 3. Report what is left ------------------------------------------------
$stillUp = @()
foreach ($port in $ports) {
    $listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($listener) { $stillUp += "$port (PID $($listener.OwningProcess -join ', '))" }
}

if ($stillUp.Count -gt 0) {
    Write-Warning "Still listening after stop: $($stillUp -join '; '). Re-run this script, or check for a launcher window it did not recognise."
} else {
    Write-Host "Done. Stopped $killed process(es); ports $($ports -join ', ') are free."
}
