[Setup]
AppId={{8E1B9C11-6A2F-4C0E-A4D1-7B2C5E9F3A21}
AppName=Knowlib
AppVersion=1.3.1
AppPublisher=Ching & 小团
AppPublisherURL=https://knowlib.fun
DefaultDirName={autopf}\Knowlib
DefaultGroupName=Knowlib
DisableProgramGroupPage=yes
OutputDir=D:\SouTi\installer
OutputBaseFilename=Knowlib-PC-1.3.1-setup
SetupIconFile=D:\SouTi\app.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\Knowlib.exe
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"

[Files]
Source: "D:\SouTi\dist\Knowlib\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Knowlib"; Filename: "{app}\Knowlib.exe"
Name: "{autodesktop}\Knowlib"; Filename: "{app}\Knowlib.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Knowlib.exe"; Description: "立即运行 Knowlib"; Flags: nowait postinstall skipifsilent






