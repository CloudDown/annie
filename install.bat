@echo off
setlocal
cd /d "%~dp0"
call "%~dp0packaging\windows\install-windows.bat" %*
