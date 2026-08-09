@echo off
set APP_DB_PATH=%TEMP%\enterprise-ai-assistant-demo.db
set SKIP_AGENT_INIT=true
set PYTHONIOENCODING=utf-8
cd /d "%~dp0.."
.venv\Scripts\python.exe -m uvicorn src.api.app:app --host 127.0.0.1 --port 8091
