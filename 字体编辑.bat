@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_editor.ps1" -NoBrowser
if errorlevel 1 (
  pause
  exit /b 1
)
start "" "http://127.0.0.1:8765/typography"
endlocal
