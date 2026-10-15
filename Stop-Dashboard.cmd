@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0dist\KV-Agent-Desk.exe" (
  "%~dp0dist\KV-Agent-Desk.exe" --stop %*
  exit /b
)
if exist "%~dp0.venv\Scripts\python.exe" (
  "%~dp0.venv\Scripts\python.exe" "%~dp0run_dashboard.py" --stop %*
  exit /b
)
exit /b 1
