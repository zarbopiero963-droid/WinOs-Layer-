; Inno Setup script for WinOs Layer
; Build on Windows with ISCC.exe after PyInstaller produces winos-api.exe
; Service name: WindowsOSLayerService — bind default 127.0.0.1:8765
; N034: SetupMutex (single-instance wizard), CSPRNG api_key + ACL, least-privilege service account.
#define MyAppName "Windows OS API Layer"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "WinOs-Layer"
#define MyAppExeName "winos-api.exe"
#define MyServiceName "WindowsOSLayerService"
#define MySetupMutex "WinOsApiSetupMutex"
#define MyAppMutex "WinOsApiAppMutex"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\WinOsApi
DefaultGroupName={#MyAppName}
OutputDir=..\output
OutputBaseFilename=WinOsApi-Setup-{#MyAppVersion}
Compression=lzma
SolidCompression=yes
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
; N034: one Setup wizard at a time; refuse a second concurrent install UI.
SetupMutex={#MySetupMutex}
AppMutex={#MyAppMutex}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\..\dist\winos-api.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\service_scripts\*"; DestDir: "{app}\service"; Flags: ignoreversion recursesubdirs
Source: "..\..\windows_os_api\control_center\index.html"; DestDir: "{app}\control_center"; Flags: ignoreversion skipifsourcedoesntexist

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Parameters: "serve --host 127.0.0.1 --port 8765"
Name: "{group}\Install Windows Service"; Filename: "{app}\service\install_nssm.bat"
Name: "{group}\Uninstall Service"; Filename: "{app}\service\uninstall_service.bat"
Name: "{group}\Uninstall"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Parameters: "serve --host 127.0.0.1 --port 8765"; Description: "Start WinOs API (localhost)"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; api_key.txt is created below in [Code] at ssPostInstall, not by [Files], so
; Inno does not track it and the uninstaller would leave it behind: a credential
; granting access to an OS-control API, surviving on disk after the user removed
; the product. Caught by scripts/installer_smoke.py on the first real uninstall
; ever performed ("uninstall left files: api_key.txt").
Type: files; Name: "{app}\api_key.txt"
; The server writes its audit log next to itself when started from {app}; that
; log records API key prefixes and executed command lines.
Type: filesandordirs; Name: "{app}\logs"
; The configured backend and the service's private TEMP/TMP create these
; directories at runtime. They contain only product-owned data below {app}.
Type: filesandordirs; Name: "{app}\sandbox"
Type: filesandordirs; Name: "{app}\tmp"
; Then drop the directory itself, once nothing of ours remains in it.
Type: dirifempty; Name: "{app}"

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
var
  KeyPath: String;
  ScriptPath: String;
  ResultCode: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    KeyPath := ExpandConstant('{app}\api_key.txt');
    // Script lives under {app}\service (packaged); key is the only Code-generated file.
    ScriptPath := ExpandConstant('{app}') + '\service\write_secure_api_key.ps1';
    if not FileExists(KeyPath) then
    begin
      if not FileExists(ScriptPath) then
        RaiseException('Missing write_secure_api_key.ps1 beside service scripts');
      if not Exec(
        'powershell.exe',
        '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + ScriptPath + '" -Path "' + KeyPath + '"',
        ExpandConstant('{app}') + '\service',
        SW_HIDE,
        ewWaitUntilTerminated,
        ResultCode
      ) then
        RaiseException('Unable to launch write_secure_api_key.ps1');
      if ResultCode <> 0 then
        RaiseException('write_secure_api_key.ps1 failed with code ' + IntToStr(ResultCode));
      if not WizardSilent then
        MsgBox('API key written to api_key.txt (localhost default).' + #13#10 +
               'Key ACL: SYSTEM / Administrators / LocalService only.' + #13#10 +
               'Set WINOS_API_KEYS and do not expose the port publicly.' + #13#10 +
               'Service name: {#MyServiceName} (NT AUTHORITY\LocalService)', mbInformation, MB_OK);
    end;
  end;
end;
