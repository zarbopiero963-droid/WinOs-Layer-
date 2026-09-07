@echo off
REM Install WinOsApi as a Windows service via NSSM (run as Administrator)
set NSSM=%~dp0nssm.exe
set APP=%~dp0winos-api.exe
"%NSSM%" install WinOsApi "%APP%" serve --host 127.0.0.1 --port 8765
"%NSSM%" set WinOsApi AppDirectory "%~dp0"
"%NSSM%" set WinOsApi DisplayName "Windows OS API Layer"
"%NSSM%" set WinOsApi Start SERVICE_AUTO_START
"%NSSM%" start WinOsApi
echo Installed WinOsApi
