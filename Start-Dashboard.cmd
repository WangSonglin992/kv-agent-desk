@echo off
setlocal
cd /d "%~dp0"
if exist "%LOCALAPPDATA%\Programs\KV-Agent-Desk\KV-Agent-Desk.exe" (
  start "" "%LOCALAPPDATA%\Programs\KV-Agent-Desk\KV-Agent-Desk.exe" %*
  exit /b 0
)
if exist "%~dp0dist\KV-Agent-Desk.exe" (
  start "" "%~dp0dist\KV-Agent-Desk.exe" %*
  exit /b 0
)
if exist "%~dp0.venv\Scripts\pythonw.exe" (
  start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0run_dashboard.py" %*
  exit /b 0
)
echo Build the app first with powershell -File Build-Dashboard.ps1
pause
exit /b 1
