@echo off
setlocal EnableExtensions
cd /d "%~dp0"
echo Installation Annie (Windows)...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\windows\install.ps1"
if errorlevel 1 (
  echo.
  echo Echec installation. Voir les messages ci-dessus.
  pause
  exit /b 1
)
echo.
echo OK. Fermez ce terminal, ouvrez-en un nouveau, puis tapez : annie
pause
