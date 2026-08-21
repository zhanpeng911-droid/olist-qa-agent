@echo off
chcp 65001 >nul
cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_agent.ps1"
if errorlevel 1 (
    echo.
    echo Agent startup failed. Check artifacts\runtime_logs\agent_stderr.log.
    pause
    exit /b 1
)
