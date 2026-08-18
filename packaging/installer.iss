; packaging/installer.iss
; Inno Setup 6 — 宝可梦自动化购买脚本 正式安装包
;
; 版本号: 由 build_installer.bat 从 version.py 读取后 /DMyAppVersion=... 传入
; (唯一版本源是 version.py, 本文件不写死版本号)
;
; 升级机制:
;   - 固定 AppId → 安装新版时自动检测旧版并覆盖程序文件
;   - 客户数据在 %LOCALAPPDATA%\PokemonAutomation, 升级/卸载均不触碰
;   - 卸载时询问是否删除历史数据(默认保留)
;
; 代码签名预留: 未配置证书时 ISCC 输出警告(CODE SIGNING NOT CONFIGURED),
; 不伪造签名。

#ifndef MyAppVersion
  #error 必须传入 MyAppVersion (build_installer.bat 自动传入)
#endif

#define MyAppName "宝可梦自动化购买脚本"
#define MyAppNameEn "PokemonAutomation"
#define MyAppPublisher "PokemonAutomation"
#define MyAppExeName "宝可梦自动化购买脚本.exe"
#define MyAppId "{{F8E0D5B7-6A2C-4E3D-9B5A-1C4E7F9A3D2B}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppNameEn}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
; 升级: 旧版本目录相同 → 覆盖安装
UsePreviousAppDir=yes
OutputDir=..\release
OutputBaseFilename={#MyAppName}_Setup_{#MyAppVersion}
SetupIconFile=icon.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; 中文界面(语言文件随仓库分发: packaging/ChineseSimplified.isl)
[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"
Name: "desktopicon\common"; Description: "所有用户"; GroupDescription: "附加任务:"

[Files]
; Release onedir 产物整体安装
Source: "..\dist\{#MyAppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
// 卸载时询问是否删除客户历史数据(默认保留 — 不触碰 LOCALAPPDATA)
// 静默卸载(/VERYSILENT)不弹窗, 默认保留数据。
function IsUninstallSilent(): Boolean;
var
  i: Integer;
begin
  Result := False;
  for i := 1 to ParamCount do
    if CompareText(ParamStr(i), '/VERYSILENT') = 0 then
    begin
      Result := True;
      break;
    end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
  Msg: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\{#MyAppNameEn}');
    if DirExists(DataDir) and (not IsUninstallSilent()) then
    begin
      Msg := '是否同时删除历史数据(日志/数据库/错误记录/截图)?'
             + Chr(13) + Chr(10) + Chr(13) + Chr(10) + DataDir
             + Chr(13) + Chr(10)
             + '建议保留, 以便重装后继续使用。';
      if MsgBox(Msg, mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      begin
        DelTree(DataDir, True, True, True);
      end;
    end;
  end;
end;

// 代码签名预留: 有证书时在 [Setup] 段加 SignTool=mysigntool
// (配置方法见 README 开发版发布说明; 当前无证书, 不伪造签名)
