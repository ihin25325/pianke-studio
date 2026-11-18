#ifndef BundleDir
  #error BundleDir must point to a freshly built portable directory
#endif
#ifndef OutputDir
  #error OutputDir must be specified
#endif
[Setup]
AppId={{F8D35343-2519-44B6-A6CA-70048C3A25F7}
AppName=片刻Studio
AppVersion=0.2.0-preview.1
VersionInfoVersion=0.2.0.0
AppPublisher=Pianke Studio contributors
AppPublisherURL=https://github.com/ihin25325/pianke-studio
DefaultDirName={localappdata}\Programs\PiankeStudio
DefaultGroupName=片刻Studio
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=PiankeStudio-0.2.0-preview.1-windows-x64-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\PiankeStudio.exe
LicenseFile={#BundleDir}\LICENSE
CloseApplications=no
RestartApplications=no
DisableProgramGroupPage=yes
[Tasks]
Name: desktopicon; Description: 创建桌面快捷方式; Flags: checkedonce
[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{autoprograms}\片刻Studio"; Filename: "{app}\PiankeStudio.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\片刻Studio"; Filename: "{app}\PiankeStudio.exe"; WorkingDir: "{app}"; Tasks: desktopicon
[Run]
Filename: "{app}\PiankeStudio.exe"; Description: 启动片刻Studio; Flags: nowait postinstall skipifsilent
[Code]
function SafeToModify(Launcher: String): Boolean;
var Code: Integer;
begin
  Result := True;
  if FileExists(Launcher) then begin
    Result := Exec(Launcher, '--can-update', ExtractFileDir(Launcher), SW_HIDE, ewWaitUntilTerminated, Code);
    Result := Result and (Code = 0);
  end;
end;
function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if not SafeToModify(ExpandConstant('{app}\PiankeStudio.exe')) then
    Result := '请先在启动器中停止后台服务，再关闭启动器，然后重试安装。不会自动结束正在制作的视频。';
end;
function InitializeUninstall(): Boolean;
begin
  Result := SafeToModify(ExpandConstant('{app}\PiankeStudio.exe'));
  if not Result then MsgBox('请先停止后台并关闭启动器，再重试卸载。', mbInformation, MB_OK);
end;
// No UninstallDelete rules: user-created data, models, sources and outputs remain.
