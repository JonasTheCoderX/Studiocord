; Inno Setup script for Studiocord-Setup.exe (Windows).
; Built by .github/workflows/release.yml after `python build.py` made dist\studiocord.exe:
;   iscc /DAppVersion=1.2.3 installer\studiocord.iss
; Studiocord by JonasTheCoderX

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{7C1F3E52-5B8A-4C4E-9D6B-2F0A9E7B1C3D}
AppName=Studiocord
AppVersion={#AppVersion}
AppVerName=Studiocord {#AppVersion}
AppPublisher=JonasTheCoderX
AppComments=Discord Rich Presence for Roblox Studio
; Per-user install: no admin prompt needed.
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\Studiocord
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
OutputDir=..\dist
OutputBaseFilename=Studiocord-Setup
SetupIconFile=studiocord.ico
UninstallDisplayIcon={app}\studiocord.exe
UninstallDisplayName=Studiocord
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=no

[Files]
Source: "..\dist\studiocord.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\config.example.toml"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion

; Starting settings file (never overwrites the user's own).
Source: "..\config.example.toml"; DestDir: "{userappdata}\Studiocord"; DestName: "config.toml"; Flags: onlyifdoesntexist uninsneveruninstall

[Registry]
; Start with Windows.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Studiocord"; ValueData: """{app}\studiocord.exe"""; Flags: uninsdeletevalue

[Icons]
Name: "{userprograms}\Studiocord\Studiocord settings"; Filename: "{userappdata}\Studiocord\config.toml"
Name: "{userprograms}\Studiocord\Studiocord log"; Filename: "{localappdata}\Studiocord\studiocord.log"
Name: "{userprograms}\Studiocord\Uninstall Studiocord"; Filename: "{uninstallexe}"

[Run]
; Start it right away (it runs in the background, no window).
Filename: "{app}\studiocord.exe"; Flags: nowait runasoriginaluser

[UninstallDelete]
; The Studio plugin it put in place, and its own log.
Type: files; Name: "{localappdata}\Roblox\Plugins\Studiocord.lua"
Type: filesandordirs; Name: "{localappdata}\Studiocord"

[Code]
procedure StopStudiocord();
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM studiocord.exe', '', SW_HIDE,
       ewWaitUntilTerminated, ResultCode);
end;

// Stop a running copy before files are replaced (updates) ...
function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopStudiocord();
  Result := '';
end;

// ... and before uninstalling.
function InitializeUninstall(): Boolean;
begin
  StopStudiocord();
  Result := True;
end;
