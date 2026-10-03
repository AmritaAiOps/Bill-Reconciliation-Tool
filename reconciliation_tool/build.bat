@echo off
rem Builds dist\EstimateVsBillReconciliation.exe. Double-click to run.
rem The work is done by build.ps1 (progress bar + percent in the title bar).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1"
echo.
pause
