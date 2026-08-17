@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Runtime not found. Please run the environment installer first.
    pause
    exit /b 1
)

echo Starting Olist QA Agent...
echo URL: http://127.0.0.1:8501

echo Checking attribution core...
".venv\Scripts\python.exe" -c "from agent_core.attribution import ATTRIBUTION_SCHEMA_VERSION; print('Attribution schema: ' + ATTRIBUTION_SCHEMA_VERSION)"
if errorlevel 1 (
    echo Attribution core failed to load.
    pause
    exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\start_agent.ps1" -NoOpenBrowser
if errorlevel 1 (
    echo Agent failed to start. See artifacts\runtime_logs for details.
    pause
    exit /b 1
)

echo Agent is ready. Opening the default browser...
start "" "http://127.0.0.1:8501"
if errorlevel 1 (
    start "" explorer.exe "http://127.0.0.1:8501"
)
echo If the browser does not open, visit: http://127.0.0.1:8501
endlocal
exit /b 0
