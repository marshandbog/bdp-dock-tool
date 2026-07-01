; Inno Setup script for the RV2900WD Dock BDP Pump Test Tool.
;
; Wraps the PyInstaller-built .exe in a standard Windows installer that installs
; to Program Files, creates Start Menu (and optional Desktop) shortcuts, and
; registers a clean uninstaller.
;
; Compile on Windows with Inno Setup 6:
;     ISCC.exe /DMyAppVersion=1.0.0 packaging\installer.iss
; The CI workflow passes the version from the git tag. If /DMyAppVersion is not
; supplied, it falls back to the default below.

#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif

#define MyAppName "BDP Dock Pump Test Tool"
#define MyAppPublisher "isit5oclock.org"
#define MyAppURL "https://isit5oclock.org"
#define MyAppExeName "BDP Dock Pump Test Tool.exe"

[Setup]
; AppId uniquely identifies this application for upgrades/uninstall. Keep it STABLE
; across releases -- do not regenerate it, or upgrades will look like new installs.
AppId={{7F3C1A54-2B9E-4D18-9F6A-BD0C7E5A1E42}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
DefaultDirName={autopf}\BDP Dock Pump Test Tool
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=Output
OutputBaseFilename=BDP-Tool-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Per-machine install needs admin; use lowest+autopf for per-user if preferred.
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
; SetupIconFile=bdp_tool.ico

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; The single-file exe produced by PyInstaller (dist\ is at the repo root in CI).
Source: "..\dist\BDP Dock Pump Test Tool.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; Offer to launch the tool when the installer finishes.
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
