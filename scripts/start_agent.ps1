param(
    [switch]$NoOpenBrowser
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$agentUrl = "http://127.0.0.1:8501"
$healthUrl = "$agentUrl/_stcore/health"

function Test-AgentHealth {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $healthUrl -TimeoutSec 2
        return $response.StatusCode -eq 200 -and $response.Content -eq "ok"
    }
    catch {
        return $false
    }
}

if (-not (Test-Path -LiteralPath $pythonPath)) {
    Write-Error "Python environment not found: $pythonPath"
    exit 1
}

if (-not (Test-AgentHealth)) {
    $logDirectory = Join-Path $projectRoot "artifacts\runtime_logs"
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $stdoutLog = Join-Path $logDirectory "agent_$stamp.stdout.log"
    $stderrLog = Join-Path $logDirectory "agent_$stamp.stderr.log"
    $arguments = @(
        "-m", "streamlit", "run", "ui\app.py",
        "--server.headless", "true",
        "--server.address", "127.0.0.1",
        "--server.port", "8501",
        "--server.fileWatcherType", "none"
    )
    $process = Start-Process `
        -FilePath $pythonPath `
        -ArgumentList $arguments `
        -WorkingDirectory $projectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutLog `
        -RedirectStandardError $stderrLog `
        -PassThru

    $ready = $false
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        Start-Sleep -Milliseconds 500
        if (Test-AgentHealth) {
            $ready = $true
            break
        }
        if ($process.HasExited) {
            break
        }
    }

    if (-not $ready) {
        Write-Error "Agent failed to become healthy. Logs: $stdoutLog ; $stderrLog"
        if (Test-Path -LiteralPath $stderrLog) {
            Get-Content -LiteralPath $stderrLog -Tail 20
        }
        exit 1
    }
    Write-Host "Agent is healthy. PID=$($process.Id) URL=$agentUrl"
}
else {
    Write-Host "Agent is already running: $agentUrl"
}

if (-not $NoOpenBrowser) {
    Start-Process -FilePath "explorer.exe" -ArgumentList $agentUrl
}

exit 0
