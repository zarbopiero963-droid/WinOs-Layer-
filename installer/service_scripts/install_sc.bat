@echo off
REM winos-api.exe is a console program, not a native ServiceMain executable.
REM Registering it directly with sc.exe creates WindowsOSLayerService but cannot
REM complete the SCM handshake (typically error 1053). NSSM is the supported host.
echo ERROR: direct sc.exe installation is unsupported for winos-api.exe. 1>&2
echo Run install_nssm.bat as Administrator instead. 1>&2
exit /b 1
