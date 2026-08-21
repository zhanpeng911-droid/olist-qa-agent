param(
    [int]$Port = 8000,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$FrontendIndex = Join-Path $ProjectRoot "web\dist\index.html"
$LogDir = Join-Path $ProjectRoot "artifacts\runtime_logs"
$BaseUrl = "http://127.0.0.1:$Port"

if (-not (Test-Path -LiteralPath $PythonExe)) {
    throw "Python virtual environment not found: $PythonExe. Install dependencies first."
}
if (-not (Test-Path -LiteralPath $FrontendIndex)) {
    throw "Frontend build not found: $FrontendIndex. Build the web project first."
}

try {
    $Existing = Invoke-WebRequest -UseBasicParsing -Uri "$BaseUrl/api/meta" -TimeoutSec 2
    if ($Existing.StatusCode -eq 200) {
        Write-Host "Agent is already running: $BaseUrl"
        if (-not $NoBrowser) { Start-Process $BaseUrl }
        exit 0
    }
} catch {
    # Continue when no server is listening yet.
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$StdoutLog = Join-Path $LogDir "agent_stdout.log"
$StderrLog = Join-Path $LogDir "agent_stderr.log"
$Arguments = @("-m", "uvicorn", "server.main:app", "--host", "127.0.0.1", "--port", "$Port")

$Process = Start-Process -FilePath $PythonExe `
    -ArgumentList $Arguments `
    -WorkingDirectory $ProjectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $StdoutLog `
    -RedirectStandardError $StderrLog `
    -PassThru

for ($Attempt = 0; $Attempt -lt 40; $Attempt++) {
    Start-Sleep -Milliseconds 500
    if ($Process.HasExited) {
        $Tail = if (Test-Path -LiteralPath $StderrLog) {
            (Get-Content -LiteralPath $StderrLog -Tail 20) -join [Environment]::NewLine
        } else { "No error log was created." }
        throw "Agent failed to start.$([Environment]::NewLine)$Tail"
    }
    try {
        $Response = Invoke-WebRequest -UseBasicParsing -Uri "$BaseUrl/api/meta" -TimeoutSec 2
        if ($Response.StatusCode -eq 200) {
            Write-Host "Agent started: $BaseUrl"
            Write-Host "Runtime logs: $LogDir"
            if (-not $NoBrowser) { Start-Process $BaseUrl }
            exit 0
        }
    } catch {
        # Backend is still initializing.
    }
}

Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
throw "Agent was not ready within 20 seconds. See: $StderrLog"
