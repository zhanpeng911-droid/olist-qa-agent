param([int]$Port=8000,[switch]$NoBrowser)
$ErrorActionPreference='Stop'
$ProjectRoot=Split-Path -Parent $PSScriptRoot
$PythonExe=Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$BaseUrl="http://127.0.0.1:$Port"
if(-not(Test-Path -LiteralPath $PythonExe)){throw 'Missing .venv. Install requirements.txt first.'}
if(-not(Test-Path -LiteralPath (Join-Path $ProjectRoot 'web\dist\index.html'))){throw 'Frontend is not built. Run pnpm build in web first.'}
try{
    $Meta=Invoke-RestMethod -Uri "$BaseUrl/api/health" -TimeoutSec 3
    if($Meta.app_version -ne '3.0'){throw 'Port is occupied by an older Agent. Stop the old process first.'}
    if(-not $NoBrowser){Start-Process $BaseUrl}
    Write-Host "Agent is running: $BaseUrl"
    exit 0
}catch{
    if($_.Exception.Message -like '*older Agent*'){throw}
}
$LogDir=Join-Path $ProjectRoot 'artifacts\runtime_logs'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Process=Start-Process -FilePath $PythonExe -ArgumentList @('-m','uvicorn','server.main:app','--host','127.0.0.1','--port',"$Port") -WorkingDirectory $ProjectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $LogDir 'agent_stdout.log') -RedirectStandardError (Join-Path $LogDir 'agent_stderr.log') -PassThru
for($Attempt=0;$Attempt -lt 60;$Attempt++){
    Start-Sleep -Milliseconds 500
    if($Process.HasExited){throw ((Get-Content -LiteralPath (Join-Path $LogDir 'agent_stderr.log') -Tail 20)-join [Environment]::NewLine)}
    try{
        $Meta=Invoke-RestMethod -Uri "$BaseUrl/api/health" -TimeoutSec 3
        if($Meta.app_version -eq '3.0'){
            if(-not $NoBrowser){Start-Process $BaseUrl}
            Write-Host "Agent started: $BaseUrl"
            exit 0
        }
    }catch{}
}
throw 'Startup timed out. Read artifacts/runtime_logs/agent_stderr.log.'
