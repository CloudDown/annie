@echo off
setlocal EnableExtensions
cd /d "%~dp0\..\.."
echo Installing Annie (Windows)...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
if errorlevel 1 (
  echo.
  echo Install failed. See messages above.
  pause
  exit /b 1
)
set "ANNIE_BIN=%LOCALAPPDATA%\Programs\Annie\bin"
if exist "%ANNIE_BIN%\annie.cmd" (
  set "PATH=%ANNIE_BIN%;%PATH%"
)
echo.
echo OK. In THIS terminal you can type: annie
echo    or from the repo: bin\annie.cmd
echo For other windows: close them, open a new terminal, then: annie
pause
