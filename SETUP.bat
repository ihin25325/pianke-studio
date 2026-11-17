@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup.ps1"
set "RESULT=%ERRORLEVEL%"
if not "%RESULT%"=="0" echo Setup failed. Read the error above. Existing videos were not deleted.
pause
exit /b %RESULT%
