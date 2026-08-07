@echo off
rem chatgpt2api image backend on 127.0.0.1:8000 - starts HIDDEN by default (2026-08-02).
rem logs: chatgpt2api\logs\server.out.log / server.err.log
rem for a visible debug console run instead: chatgpt2api\.venv\Scripts\python.exe main.py
if not exist "%~dp0chatgpt2api\logs" mkdir "%~dp0chatgpt2api\logs"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~dp0chatgpt2api\.venv\Scripts\python.exe' -ArgumentList 'main.py' -WorkingDirectory '%~dp0chatgpt2api' -WindowStyle Hidden -RedirectStandardOutput '%~dp0chatgpt2api\logs\server.out.log' -RedirectStandardError '%~dp0chatgpt2api\logs\server.err.log'"
