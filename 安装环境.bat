@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo 正在创建隔离的 Python 环境...
python -m venv --system-site-packages .venv
if errorlevel 1 goto :error

echo 正在检查 Agent 依赖...
".venv\Scripts\python.exe" -c "import pandas, scipy, statsmodels, streamlit, yaml, dotenv, openai, pymysql"
if errorlevel 1 (
    echo 缺少部分依赖，正在下载安装...
    ".venv\Scripts\python.exe" -m pip install "pandas>=2.3" "scipy>=1.13" "statsmodels>=0.14.2" "streamlit>=1.32" "pyyaml>=6.0.1" "python-dotenv>=1.2.2" "openai>=3.0" "pymysql>=1.2"
    if errorlevel 1 goto :error
)

echo.
echo 安装完成。以后双击“启动Agent.bat”即可。
pause
exit /b 0

:error
echo.
echo 安装失败，请保留本窗口中的错误信息。
pause
exit /b 1
