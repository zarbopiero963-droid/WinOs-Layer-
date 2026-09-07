; Inno Setup script for WinOs Layer
; Build on Windows with ISCC.exe after PyInstaller produces winos-api.exe
#define MyAppName "Windows OS API Layer"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "WinOs-Layer"
#define MyAppExeName "winos-api.exe"

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
ArchitecturesInstallIn64BitMode=x64

[Files]
Source: "..\..\dist\winos-api.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\windows_os_api\installer\*.bat"; DestDir: "{app}\service"; Flags: ignoreversion skipifsourcedoesntexist

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Parameters: "serve"
Name: "{group}\Uninstall"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Parameters: "serve --host 127.0.0.1 --port 8765"; Description: "Start WinOs API"; Flags: nowait postinstall skipifsilent
