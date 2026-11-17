@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run_tests.ps1"
set "RESULT=%ERRORLEVEL%"
pause
exit /b %RESULT%
