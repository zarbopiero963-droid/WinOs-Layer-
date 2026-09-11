@echo off
setlocal EnableExtensions
set "SERVICE=WindowsOSLayerService"

sc.exe query "%SERVICE%" >nul 2>&1
if errorlevel 1 (
  echo %SERVICE% is not installed.
  exit /b 0
)

sc.exe stop "%SERVICE%" >nul 2>&1
for /L %%I in (1,1,30) do (
  powershell.exe -NoProfile -NonInteractive -Command "$service = Get-Service -Name '%SERVICE%' -ErrorAction SilentlyContinue; if ($null -ne $service -and $service.Status -eq [ServiceProcess.ServiceControllerStatus]::Stopped) { exit 0 }; exit 1"
  if not errorlevel 1 goto :stopped
  powershell.exe -NoProfile -NonInteractive -Command "Start-Sleep -Seconds 1"
)
echo ERROR: %SERVICE% did not reach STOPPED within 30 seconds. 1>&2
exit /b 1

:stopped
sc.exe delete "%SERVICE%" >nul 2>&1
if errorlevel 1 (
  echo ERROR: sc.exe could not delete %SERVICE%. 1>&2
  exit /b 1
)
for /L %%I in (1,1,30) do (
  sc.exe query "%SERVICE%" >nul 2>&1 || goto :removed
  powershell.exe -NoProfile -NonInteractive -Command "Start-Sleep -Seconds 1"
)
echo ERROR: %SERVICE% is still registered after delete. 1>&2
exit /b 1

:removed
echo Removed %SERVICE%.
exit /b 0
