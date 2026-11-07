@echo off
setlocal EnableExtensions DisableDelayedExpansion
rem ASCII-only .BAT file. Do not save as UTF-8 BOM.
rem Set UTF-8 console mode for paths, logs and subprocess output.
chcp 65001 >nul
cd /d "%~dp0"
title NotabilityEditor 0.7.9-Beta - Windows Setup
echo ============================================================
echo          NotabilityEditor 0.7.9-Beta - SETUP
echo ============================================================
echo.
echo Step 1: Prepare Python 3.11 environment.
echo Step 2: Install dependencies and build standalone EXE.
echo Step 3: Install app and create shortcuts.
echo.
if not exist "%~dp0deploy_windows.ps1" (
    echo [ERROR] deploy_windows.ps1 not found.
    echo Please extract ALL files from the ZIP, then run INSTALL.bat.
    echo.
    pause
    exit /b 1
)
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy_windows.ps1"
set "RESULT=%ERRORLEVEL%"
echo.
if not "%RESULT%"=="0" (
    echo [FAILED] Setup exited with code %RESULT%.
    echo Logs: deploy_logs folder next to INSTALL.bat.
) else (
    echo [SUCCESS] Setup complete. Check desktop for the shortcut.
)
echo.
pause
exit /b %RESULT%
