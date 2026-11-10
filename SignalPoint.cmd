@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Run scripts\setup.ps1 first to install SignalPoint.
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -m signalpoint.desktop %*
