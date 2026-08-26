; 成长股分析套件 — 单文件安装包（Inno Setup 6）
; 编译：iscc installer\StockAnalysis.iss
; 或：python pack.py（onedir 完成后自动调用）

#define MyAppName "成长股分析套件"
#define MyAppNameEn "StockAnalysis"
#define MyAppVersion "2026.08.17"
#define MyAppPublisher "StockAnalysis"
#define MyAppExeName "StockAnalysis.exe"

[Setup]
AppId={{8C3E1B7A-4D2F-4A91-9E6C-A1B2C3D4E5F6}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\{#MyAppNameEn}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist\installer
OutputBaseFilename=StockAnalysisSetup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}
SetupLogging=yes
CloseApplications=no
RestartApplications=no
DirExistsWarning=no

[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式（打开图形界面）"; GroupDescription: "附加图标："; Flags: checkedonce

[Files]
; PyInstaller onedir：exe + _internal。不含开发机 data/docs 巨量 PDF。
Source: "..\dist\StockAnalysis\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\StockAnalysis\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\dist\StockAnalysis\config.yaml"; DestDir: "{app}"; Flags: ignoreversion onlyifdoesntexist
Source: "..\dist\StockAnalysis\使用说明.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\StockAnalysis\userdata\watchlist.txt"; DestDir: "{app}\userdata"; Flags: ignoreversion onlyifdoesntexist
Source: "..\dist\StockAnalysis\userdata\settings.yaml"; DestDir: "{app}\userdata"; Flags: ignoreversion onlyifdoesntexist
Source: "..\dist\StockAnalysis\userdata\README.md"; DestDir: "{app}\userdata"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Comment: "打开成长股分析套件"
Name: "{group}\使用说明"; Filename: "{app}\使用说明.txt"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon; Comment: "打开成长股分析套件"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即打开图形界面"; Flags: nowait postinstall skipifsilent; WorkingDir: "{app}"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\data\cache"
Type: files; Name: "{app}\userdata\daemon.log"
Type: files; Name: "{app}\userdata\last_report.json"
