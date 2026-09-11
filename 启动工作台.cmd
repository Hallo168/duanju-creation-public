@echo off
setlocal
chcp 65001 >nul
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\storyboard-workbench\start-workbench.ps1" %*
set "WORKBENCH_EXIT_CODE=%errorlevel%"
if not "%WORKBENCH_EXIT_CODE%"=="0" pause
exit /b %WORKBENCH_EXIT_CODE%
