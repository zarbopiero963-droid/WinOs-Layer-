@echo off
REM Install WindowsOSLayerService as a Windows service via NSSM (run as Administrator)
set NSSM=%~dp0nssm.exe
set APP=%~dp0winos-api.exe
"%NSSM%" install WindowsOSLayerService "%APP%" serve --host 127.0.0.1 --port 8765
"%NSSM%" set WindowsOSLayerService AppDirectory "%~dp0"
"%NSSM%" set WindowsOSLayerService DisplayName "Windows OS API Layer"
"%NSSM%" set WindowsOSLayerService Start SERVICE_AUTO_START
"%NSSM%" start WindowsOSLayerService
echo Installed WindowsOSLayerService
