@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

set "VENV_PY=%ROOT%\.venv\Scripts\python.exe"
if exist "%VENV_PY%" (
  call "%VENV_PY%" "%ROOT%\annie.py" %*
  exit /b %ERRORLEVEL%
)

for %%V in (313 312 311) do (
  set "CAND=%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
  if exist "!CAND!" (
    call "!CAND!" "%ROOT%\annie.py" %*
    exit /b !ERRORLEVEL!
  )
)

where py >nul 2>&1
if not errorlevel 1 (
  call py -3 "%ROOT%\annie.py" %*
  exit /b %ERRORLEVEL%
)

where python >nul 2>&1
if not errorlevel 1 (
  call python "%ROOT%\annie.py" %*
  exit /b %ERRORLEVEL%
)

echo Python introuvable.
echo Lancez : install-windows.bat
exit /b 1
