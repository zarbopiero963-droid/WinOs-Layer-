@echo off
sc stop WindowsOSLayerService
sc delete WindowsOSLayerService
echo Removed WindowsOSLayerService
