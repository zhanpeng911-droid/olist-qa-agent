@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo 尚未安装运行环境，请先双击“安装环境.bat”。
    pause
    exit /b 1
)

echo [1/2] 正在运行单元与集成测试...
".venv\Scripts\python.exe" -m pytest tests -q
if errorlevel 1 (
    echo.
    echo 单元测试未通过，请保留本窗口中的失败信息。
    pause
    exit /b 1
)

echo.
echo [2/2] 正在运行确定性核心问题评测...
".venv\Scripts\python.exe" tests\run_eval.py
if errorlevel 1 (
    echo.
    echo 核心问题评测未通过，请保留本窗口中的 FAIL 题号。
    pause
    exit /b 1
)

echo.
echo 全部自动测试通过。注意：这不等于真实 MySQL 和 DeepSeek 已完成验收。
pause
