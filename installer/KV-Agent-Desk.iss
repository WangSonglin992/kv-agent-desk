#ifndef AppVersion
  #define AppVersion "0.7.5"
#endif
#define SetupPayload GetEnv("KV_SETUP_PAYLOAD")

[Setup]
AppId={{8D8918EF-160F-4D1E-AE68-E168DB0AD2B9}
AppName=KV Agent Desk
AppVersion={#AppVersion}
AppVerName=KV Agent Desk {#AppVersion}
AppPublisher=KV Agent Desk
DefaultDirName={code:GetDefaultDir}
DefaultGroupName=KV Agent Desk
UsePreviousAppDir=yes
UsePreviousTasks=yes
DisableDirPage=no
DisableProgramGroupPage=no
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Uninstallable=yes
UninstallDisplayName=KV Agent Desk
UninstallDisplayIcon={app}\KV-Agent-Desk.exe
SetupIconFile=..\agent_dashboard\assets\app.ico
OutputDir=..\dist
OutputBaseFilename=KV-Agent-Desk-Setup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=no
RestartIfNeededByRun=no
VersionInfoVersion={#AppVersion}.0
VersionInfoProductName=KV Agent Desk
VersionInfoProductVersion={#AppVersion}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional options:"
Name: "autostart"; Description: "Start KV Agent Desk when I sign in"; GroupDescription: "Additional options:"

[Files]
; The temporary copy is used for the pre-replacement backup and graceful stop.
Source: "{#SetupPayload}\KV-Agent-Desk.exe"; Flags: dontcopy
Source: "{#SetupPayload}\KV-Agent-Desk.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SetupPayload}\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SetupPayload}\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SetupPayload}\licenses\*"; DestDir: "{app}\licenses"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\KV Agent Desk"; Filename: "{app}\KV-Agent-Desk.exe"; Parameters: "--state-dir ""{%USERPROFILE}\.kv-agent-desk"""; WorkingDir: "{app}"
Name: "{userdesktop}\KV Agent Desk"; Filename: "{app}\KV-Agent-Desk.exe"; Parameters: "--state-dir ""{%USERPROFILE}\.kv-agent-desk"""; WorkingDir: "{app}"; Tasks: desktopicon

[Code]
const
  InstallRegistrySubkey = 'Software\KV-Agent-Desk';
  InstallIniSection = 'Installation';
  InstallIniName = 'installation.ini';

var
  SavedInstallDir: string;
  SavedDesktopShortcut: string;
  DirectoryDefaultApplied: Boolean;
  FinishFailed: Boolean;

function InstallationIniPath(): string;
begin
  Result := ExpandConstant('{%USERPROFILE}\.kv-agent-desk\' + InstallIniName);
end;

function IsAbsoluteInstallDir(const Value: string): Boolean;
begin
  Result := False;
  if Length(Value) < 4 then
    Exit;
  if not (((Value[1] >= 'A') and (Value[1] <= 'Z')) or
     ((Value[1] >= 'a') and (Value[1] <= 'z'))) then
    Exit;
  if (Value[2] <> ':') or (Value[3] <> '\') then
    Exit;
  if (Value[Length(Value)] = '\') or (Pos('"', Value) > 0) or
     (Pos('<', Value) > 0) or (Pos('>', Value) > 0) or
     (Pos('|', Value) > 0) or (Pos('?', Value) > 0) or
     (Pos('*', Value) > 0) or (Pos(':', Copy(Value, 3, Length(Value))) > 0) or
     (Pos(#13, Value) > 0) or (Pos(#10, Value) > 0) then
    Exit;
  Result := True;
end;

function ReadInstallationIni(var InstallDir, DesktopShortcut: string): Boolean;
var
  IniPath, InstallerManaged: string;
begin
  Result := False;
  IniPath := InstallationIniPath();
  if not FileExists(IniPath) then
    Exit;

  InstallDir := GetIniString(InstallIniSection, 'InstallDir', '', IniPath);
  DesktopShortcut := GetIniString(InstallIniSection, 'DesktopShortcut', '', IniPath);
  InstallerManaged := GetIniString(InstallIniSection, 'InstallerManaged', '', IniPath);
  if not IsAbsoluteInstallDir(InstallDir) then
    Exit;
  if (DesktopShortcut <> '0') and (DesktopShortcut <> '1') then
    Exit;
  if (InstallerManaged <> '0') and (InstallerManaged <> '1') then
    Exit;
  Result := True;
end;

function InitializeSetup(): Boolean;
var
  IniPath, RegistryDir: string;
begin
  IniPath := InstallationIniPath();
  Result := True;
  FinishFailed := False;
  if FileExists(IniPath) or DirExists(IniPath) then begin
    if not ReadInstallationIni(SavedInstallDir, SavedDesktopShortcut) then begin
      SuppressibleMsgBox(
        'KV Agent Desk could not read its installation record. Setup stopped to preserve that record and the existing installation.',
        mbError, MB_OK, IDOK);
      Result := False;
      Exit;
    end;
  end else if RegQueryStringValue(HKEY_CURRENT_USER, InstallRegistrySubkey, 'InstallDir', RegistryDir) and
     IsAbsoluteInstallDir(RegistryDir) then begin
    SavedInstallDir := RegistryDir;
  end;
end;

function GetDefaultDir(Param: string): string;
var
  RegistryDir: string;
begin
  if ReadInstallationIni(SavedInstallDir, SavedDesktopShortcut) then begin
    Result := SavedInstallDir;
    Exit;
  end;

  if (SavedInstallDir <> '') and IsAbsoluteInstallDir(SavedInstallDir) then begin
    Result := SavedInstallDir;
    Exit;
  end;

  if RegQueryStringValue(HKEY_CURRENT_USER, InstallRegistrySubkey, 'InstallDir', RegistryDir) and
     IsAbsoluteInstallDir(RegistryDir) then begin
    SavedInstallDir := RegistryDir;
    Result := RegistryDir;
    Exit;
  end;

  Result := 'D:\APP\KV-Agent-Desk';
end;

function HasDirCommandLineOverride(): Boolean;
var
  Index: Integer;
  Argument: string;
begin
  Result := False;
  for Index := 1 to ParamCount do begin
    Argument := UpperCase(ParamStr(Index));
    if (Copy(Argument, 1, 5) = '/DIR=') or
       (Copy(Argument, 1, 5) = '-DIR=') then begin
      Result := True;
      Exit;
    end;
  end;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if (CurPageID = wpSelectDir) and not DirectoryDefaultApplied then begin
    if not HasDirCommandLineOverride() then
      WizardForm.DirEdit.Text := GetDefaultDir('');
    DirectoryDefaultApplied := True;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): string;
var
  TemporaryExe: string;
  ResultCode: Integer;
begin
  Result := '';
  ExtractTemporaryFile('KV-Agent-Desk.exe');
  TemporaryExe := ExpandConstant('{tmp}\KV-Agent-Desk.exe');
  if not Exec(TemporaryExe, '--installer-prepare', '', SW_HIDE,
     ewWaitUntilTerminated, ResultCode) then begin
    Result := 'KV Agent Desk could not prepare the current installation. The existing app files were left in place.';
    Exit;
  end;
  if ResultCode <> 0 then
    Result := 'KV Agent Desk could not prepare the current installation. Setup stopped before replacing any app files.';
end;

function NormalizeExistingExecutable(const Filename: string): string;
begin
  Result := '';
  if not FileExists(Filename) then
    Exit;
  Result := PathNormalizeSlashes(GetShortName(ExpandFileName(Filename)));
end;

procedure RemoveOwnedDesktopShortcut;
var
  Shell, Shortcut: Variant;
  ShortcutFilename, TargetPath, CurrentExecutable, PreviousExecutable: string;
  NormalizedTarget, NormalizedCurrent, NormalizedPrevious: string;
begin
  ShortcutFilename := ExpandConstant('{userdesktop}\KV Agent Desk.lnk');
  if not FileExists(ShortcutFilename) then
    Exit;

  try
    Shell := CreateOleObject('WScript.Shell');
    Shortcut := Shell.CreateShortcut(ShortcutFilename);
    TargetPath := Shortcut.TargetPath;
  except
    FinishFailed := True;
    RaiseException('KV Agent Desk could not verify desktop shortcut ownership.');
    Exit;
  end;

  if not IsAbsoluteInstallDir(ExtractFileDir(TargetPath)) then
    Exit;
  CurrentExecutable := ExpandConstant('{app}\KV-Agent-Desk.exe');
  NormalizedTarget := NormalizeExistingExecutable(TargetPath);
  NormalizedCurrent := NormalizeExistingExecutable(CurrentExecutable);
  if (NormalizedTarget <> '') and (NormalizedCurrent <> '') and
     PathSame(NormalizedTarget, NormalizedCurrent) then begin
    if not DeleteFile(ShortcutFilename) then begin
      FinishFailed := True;
      RaiseException('KV Agent Desk could not remove its desktop shortcut.');
    end;
    Exit;
  end;

  if (SavedInstallDir <> '') and IsAbsoluteInstallDir(SavedInstallDir) then begin
    PreviousExecutable := SavedInstallDir + '\KV-Agent-Desk.exe';
    NormalizedPrevious := NormalizeExistingExecutable(PreviousExecutable);
    if (NormalizedTarget <> '') and (NormalizedPrevious <> '') and
       PathSame(NormalizedTarget, NormalizedPrevious) then begin
      if not DeleteFile(ShortcutFilename) then begin
        FinishFailed := True;
        RaiseException('KV Agent Desk could not remove its previous desktop shortcut.');
      end;
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Parameters: string;
  DesktopShortcutChoice: string;
  AutoStartChoice: string;
  ResultCode: Integer;
begin
  if CurStep = ssPostInstall then begin
    FinishFailed := True;
    if WizardIsTaskSelected('desktopicon') then
      DesktopShortcutChoice := '1'
    else
      DesktopShortcutChoice := '0';
    if WizardIsTaskSelected('autostart') then
      AutoStartChoice := '1'
    else
      AutoStartChoice := '0';
    Parameters := '--installer-finish --install-dir "' + ExpandConstant('{app}') +
      '" --desktop-shortcut ' + DesktopShortcutChoice +
      ' --auto-start ' + AutoStartChoice;
    Log('KV task desktop=' + DesktopShortcutChoice + ' autostart=' + AutoStartChoice);

    if DesktopShortcutChoice = '0' then
      RemoveOwnedDesktopShortcut;

    if not Exec(ExpandConstant('{app}\KV-Agent-Desk.exe'), Parameters, '', SW_HIDE,
       ewWaitUntilTerminated, ResultCode) then begin
      FinishFailed := True;
      RaiseException('KV Agent Desk could not save the selected installation options. Run Setup again to repair this installation.');
    end;
    if ResultCode <> 0 then begin
      FinishFailed := True;
      RaiseException('KV Agent Desk could not save the selected installation options. Run Setup again to repair this installation.');
    end;
    FinishFailed := False;
  end;
end;

function GetCustomSetupExitCode(): Integer;
begin
  if FinishFailed then
    Result := 73
  else
    Result := 0;
end;

function InitializeUninstall(): Boolean;
var
  ResultCode: Integer;
begin
  Result := False;
  if not Exec(ExpandConstant('{app}\KV-Agent-Desk.exe'), '--installer-uninstall', '',
     SW_HIDE, ewWaitUntilTerminated, ResultCode) then begin
    SuppressibleMsgBox(
      'KV Agent Desk could not complete its uninstall cleanup. Setup left the app files and user data in place.',
      mbError, MB_OK, IDOK);
    Exit;
  end;
  if ResultCode <> 0 then begin
    SuppressibleMsgBox(
      'KV Agent Desk could not complete its uninstall cleanup. Setup left the app files and user data in place.',
      mbError, MB_OK, IDOK);
    Exit;
  end;
  Result := True;
end;
