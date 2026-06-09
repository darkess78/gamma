$ErrorActionPreference = "Stop"

$packageRoot = "C:\Users\darke\Documents\Projects\ai terminal\gamma\data\GPT-SoVITS-win\GPT-SoVITS-v3lora-20250228"
$port = 9881

$listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if (-not $listener) {
    Write-Output "No GPT-SoVITS process is listening on port $port."
    exit 0
}

$process = Get-Process -Id $listener.OwningProcess -ErrorAction SilentlyContinue
if (-not $process) {
    Write-Output "No accessible process found for port $port."
    exit 0
}

if ($process.Path -notlike "$packageRoot*") {
    throw "Port $port is owned by a different process: $($process.Path)"
}

Stop-Process -Id $process.Id -Force
Write-Output "Stopped GPT-SoVITS on port $port."
