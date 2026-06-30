@echo off
setlocal EnableExtensions
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

set "VENV_PY=%ROOT%\.venv\Scripts\python.exe"
if exist "%VENV_PY%" (
  "%VENV_PY%" "%ROOT%\annie.py" %*
  exit /b %ERRORLEVEL%
)

where python >nul 2>&1
if errorlevel 1 (
  echo Python 3.11+ requis. Installez-le depuis https://www.python.org/downloads/
  echo Puis relancez : packaging\windows\install.ps1
  exit /b 1
)

python "%ROOT%\annie.py" %*
exit /b %ERRORLEVEL%
