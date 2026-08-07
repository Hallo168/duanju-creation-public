@echo off
rem foreground/debug variant of 启动代理.cmd - visible console, live output
setlocal
cd /d "%~dp0"
uv run python launch.py %*
