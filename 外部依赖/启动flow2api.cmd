@echo off
rem flow2api (Banana 2 channel) on 127.0.0.1:8001 - starts HIDDEN by default (2026-08-02).
rem logs: flow2api\logs\server.out.log / server.err.log
rem for a visible debug console run instead: flow2api\.venv\Scripts\python.exe main.py
if not exist "%~dp0flow2api\logs" mkdir "%~dp0flow2api\logs"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~dp0flow2api\.venv\Scripts\python.exe' -ArgumentList 'main.py' -WorkingDirectory '%~dp0flow2api' -WindowStyle Hidden -RedirectStandardOutput '%~dp0flow2api\logs\server.out.log' -RedirectStandardError '%~dp0flow2api\logs\server.err.log'"
