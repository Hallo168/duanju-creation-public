@echo off
rem TikTok Symphony proxy on 127.0.0.1:8765 - starts HIDDEN by default (2026-08-02).
rem logs: logs\proxy.out.log / proxy.err.log
rem for a visible debug console use: 启动代理-前台.cmd
if not exist "%~dp0logs" mkdir "%~dp0logs"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath 'uv' -ArgumentList 'run','python','launch.py' -WorkingDirectory '%~dp0.' -WindowStyle Hidden -RedirectStandardOutput '%~dp0logs\proxy.out.log' -RedirectStandardError '%~dp0logs\proxy.err.log'"
