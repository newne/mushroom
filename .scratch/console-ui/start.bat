@echo off
rem One-click launcher for the patrol console prototype on Windows.
rem Double-click this file, or run: start.bat --port 9001
rem
rem NOTE: this file is intentionally ASCII-only. A .bat with non-ASCII
rem comments/filenames gets mangled by the console codepage, which has bitten
rem us before on this machine.
setlocal
cd /d "%~dp0"

set "PY=C:\Users\niucg1\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if not exist "%PY%" where py >nul 2>nul && set "PY=py"
if not exist "%PY%" where python >nul 2>nul && set "PY=python"
if not exist "%PY%" (
  echo [error] Python 3 not found. Edit the PY variable in this file.
  pause
  exit /b 1
)

echo Starting patrol console prototype...
"%PY%" serve.py --host 0.0.0.0 --port 8800 %*
echo.
echo Server stopped.
pause
