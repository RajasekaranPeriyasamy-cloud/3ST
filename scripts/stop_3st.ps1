# Stop 3ST API and Vite dev servers.
$Root = Split-Path $PSScriptRoot -Parent
$ports = @(8001, 8080, 8081, 8082, 5173)
foreach ($port in $ports) {
    Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        ForEach-Object {
            Write-Host "Stopping PID $($_.OwningProcess) on port $port"
            taskkill /F /PID $_.OwningProcess /T 2>$null
        }
}
Remove-Item (Join-Path $Root "data\logs\.api_running") -Force -ErrorAction SilentlyContinue
Write-Host "Done."
