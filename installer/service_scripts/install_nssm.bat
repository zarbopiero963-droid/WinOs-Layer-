@echo off
setlocal EnableExtensions DisableDelayedExpansion
REM Install WindowsOSLayerService through NSSM. Run this script as Administrator.
set "SERVICE=WindowsOSLayerService"
set "SCRIPT_DIR=%~dp0"

REM Setup.exe installs scripts under service\ and the EXE one directory above.
REM The fallback supports the portable ZIP, where scripts and EXE are siblings.
set "APP=%SCRIPT_DIR%..\winos-api.exe"
if not exist "%APP%" set "APP=%SCRIPT_DIR%winos-api.exe"
if not exist "%APP%" (
  echo ERROR: winos-api.exe not found beside or above this script. 1>&2
  exit /b 1
)
for %%I in ("%APP%") do set "APP=%%~fI"
for %%I in ("%APP%") do set "APP_DIR=%%~dpI"

REM Never put the secret in the service command line or registry. The CLI reads
REM the installer-generated file at startup from the service working directory.
if not exist "%APP_DIR%api_key.txt" (
  echo ERROR: api_key.txt is missing beside winos-api.exe. 1>&2
  exit /b 1
)
set "WINOS_SERVICE_KEY_FILE=%APP_DIR%api_key.txt"
powershell.exe -NoProfile -NonInteractive -Command "$lines = @(Get-Content -LiteralPath $env:WINOS_SERVICE_KEY_FILE); if ($lines.Count -ne 1 -or [string]::IsNullOrWhiteSpace($lines[0])) { exit 1 }"
if errorlevel 1 (
  echo ERROR: api_key.txt must contain exactly one non-empty line. 1>&2
  exit /b 1
)
set "WINOS_SERVICE_KEY_FILE="

set "PORT_NUMBER="
for /f "delims=" %%P in ('powershell.exe -NoProfile -NonInteractive -Command "$port = 8765; $raw = $env:WINOS_SERVICE_PORT; if (-not [string]::IsNullOrWhiteSpace($raw) -and (-not [int]::TryParse($raw, [ref]$port) -or $port -lt 1 -or $port -gt 65535)) { exit 1 }; Write-Output $port"') do set "PORT_NUMBER=%%P"
if not defined PORT_NUMBER goto :bad_port
set "WINOS_SERVICE_HEALTH_PORT=%PORT_NUMBER%"
powershell.exe -NoProfile -NonInteractive -Command "$client = New-Object Net.Sockets.TcpClient; try { $client.Connect('127.0.0.1', [int]$env:WINOS_SERVICE_HEALTH_PORT); exit 1 } catch { exit 0 } finally { $client.Dispose() }"
if errorlevel 1 (
  echo ERROR: 127.0.0.1:%PORT_NUMBER% is already accepting connections. 1>&2
  exit /b 1
)

set "NSSM=%SCRIPT_DIR%nssm.exe"
if not exist "%NSSM%" goto :missing_nssm

if not exist "%APP_DIR%logs" mkdir "%APP_DIR%logs"
if errorlevel 1 (
  echo ERROR: unable to create the service log directory. 1>&2
  exit /b 1
)

sc.exe query "%SERVICE%" >nul 2>&1
if not errorlevel 1 (
  echo ERROR: %SERVICE% already exists; refusing to replace it. 1>&2
  exit /b 1
)

"%NSSM%" install "%SERVICE%" "%APP%" || goto :rollback
"%NSSM%" set "%SERVICE%" AppDirectory "%APP_DIR%" || goto :rollback
"%NSSM%" set "%SERVICE%" AppParameters "serve --host 127.0.0.1 --port %PORT_NUMBER% --api-key-file api_key.txt" || goto :rollback
"%NSSM%" set "%SERVICE%" DisplayName "Windows OS API Layer" || goto :rollback
"%NSSM%" set "%SERVICE%" Description "FastAPI Windows OS API Layer - localhost only" || goto :rollback
"%NSSM%" set "%SERVICE%" Start SERVICE_AUTO_START || goto :rollback
REM Try CTRL_C_EVENT first so uvicorn executes its lifespan shutdown. Skip GUI
REM messages, retain TerminateProcess only as a last-resort safety fallback.
"%NSSM%" set "%SERVICE%" AppStopMethodSkip 6 || goto :rollback
"%NSSM%" set "%SERVICE%" AppStopMethodConsole 15000 || goto :rollback
"%NSSM%" set "%SERVICE%" AppKillProcessTree 1 || goto :rollback
"%NSSM%" set "%SERVICE%" AppStdout "%APP_DIR%logs\service.log" || goto :rollback
"%NSSM%" set "%SERVICE%" AppStderr "%APP_DIR%logs\service.log" || goto :rollback
"%NSSM%" set "%SERVICE%" AppRotateFiles 1 || goto :rollback
"%NSSM%" start "%SERVICE%" || goto :rollback
powershell.exe -NoProfile -NonInteractive -Command "$deadline = (Get-Date).AddSeconds(45); do { try { $health = Invoke-RestMethod -UseBasicParsing -Uri ('http://127.0.0.1:' + $env:WINOS_SERVICE_HEALTH_PORT + '/v1/health') -TimeoutSec 2; if ($health.status -eq 'ok') { exit 0 } } catch {}; Start-Sleep -Milliseconds 500 } while ((Get-Date) -lt $deadline); exit 1"
if errorlevel 1 goto :rollback
set "WINOS_SERVICE_HEALTH_PORT="
echo Installed and started %SERVICE% on 127.0.0.1:%PORT_NUMBER%.
exit /b 0

:bad_port
echo ERROR: WINOS_SERVICE_PORT must be an integer from 1 through 65535. 1>&2
exit /b 1

:missing_nssm
echo ERROR: the native nssm.exe was not found beside this script. 1>&2
echo Use an official artifact or copy the real NSSM binary beside this file. 1>&2
exit /b 1

:rollback
echo ERROR: service installation failed; rolling back %SERVICE%. 1>&2
if exist "%APP_DIR%logs\service.log" type "%APP_DIR%logs\service.log" 1>&2
call "%SCRIPT_DIR%uninstall_service.bat" >nul 2>&1
if errorlevel 1 (
  "%NSSM%" stop "%SERVICE%" >nul 2>&1
  "%NSSM%" remove "%SERVICE%" confirm >nul 2>&1
)
exit /b 1
