@echo off
setlocal EnableExtensions
cd /d "%~dp0\..\.."
echo Installation Annie (Windows)...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
if errorlevel 1 (
  echo.
  echo Echec installation. Voir les messages ci-dessus.
  pause
  exit /b 1
)
set "ANNIE_BIN=%LOCALAPPDATA%\Programs\Annie\bin"
if exist "%ANNIE_BIN%\annie.cmd" (
  set "PATH=%ANNIE_BIN%;%PATH%"
)
echo.
echo OK. Dans CE terminal vous pouvez taper : annie
echo    ou depuis le depot : bin\annie.cmd
echo Pour les autres fenetres : fermez-les et rouvrez un terminal, puis : annie
pause
