#define MyAppName "Conjure Crosshair"
#ifndef MyAppVersion
	#define MyAppVersion "1.0.0"
#endif
#ifndef MyOutputDir
	#define MyOutputDir "dist"
#endif
#define MyAppPublisher "Conjure Crosshair"
#define MyAppExeName "Conjure Crosshair.exe"

[Setup]
AppId={{B8D9B9C1-9A8C-4E5E-9A7D-2E00B24C4B31}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\Conjure Crosshair
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
UsePreviousAppDir=yes
CloseApplications=yes
CloseApplicationsFilter=Conjure Crosshair.exe
RestartApplications=no
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#MyOutputDir}
OutputBaseFilename=Conjure Crosshair
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma
SolidCompression=yes
WizardStyle=modern

[Tasks]
Name: "startup"; Description: "Launch Conjure Crosshair when Windows starts"; GroupDescription: "Startup options:"; Flags: unchecked

[Files]
Source: "dist\Conjure Crosshair.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{commonstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: startup

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent