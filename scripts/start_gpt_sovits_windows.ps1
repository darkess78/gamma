$ErrorActionPreference = "Stop"

$packageRoot = "C:\Users\darke\Documents\Projects\ai terminal\gamma\data\GPT-SoVITS-win\GPT-SoVITS-v3lora-20250228"
$pythonExe = Join-Path $packageRoot "runtime\python.exe"
$stdoutLog = Join-Path $packageRoot "api-9881.out.log"
$stderrLog = Join-Path $packageRoot "api-9881.err.log"
$port = 9881

if (-not (Test-Path $pythonExe)) {
    throw "GPT-SoVITS runtime not found at $pythonExe"
}

$existing = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    $process = Get-Process -Id $existing.OwningProcess -ErrorAction SilentlyContinue
    if ($process -and $process.Path -like "$packageRoot*") {
        Write-Output "GPT-SoVITS is already listening on port $port."
        exit 0
    }
    throw "Port $port is already in use by another process."
}

$env:PYTHONIOENCODING = "utf-8"
$escapedPython = '"' + $pythonExe + '"'
$escapedStdout = '"' + $stdoutLog + '"'
$escapedStderr = '"' + $stderrLog + '"'
$cmdLine = '/c cd /d "' + $packageRoot + '" && start "" /b ' + $escapedPython + ' api_v2.py -a 127.0.0.1 -p ' + $port + ' -c GPT_SoVITS/configs/tts_infer.yaml 1>>' + $escapedStdout + ' 2>>' + $escapedStderr
& cmd.exe $cmdLine | Out-Null

Start-Sleep -Seconds 5
$listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if (-not $listener) {
    throw "GPT-SoVITS did not start. Check $stdoutLog and $stderrLog."
}

Write-Output "GPT-SoVITS API is listening on http://127.0.0.1:$port/tts"
