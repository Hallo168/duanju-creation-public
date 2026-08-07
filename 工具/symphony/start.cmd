@echo off
setlocal
cd /d "%~dp0"
set SYMPHONY_DIRECT_ONLY=1
uv run uvicorn app.main:app --host 127.0.0.1 --port 8765
