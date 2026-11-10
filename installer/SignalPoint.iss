; Build through scripts/build_installer.py so version, artwork and bundle are checked.
#ifndef BundleDir
  #error BundleDir is required
#endif
#ifndef AppVersion
  #error AppVersion is required
#endif
#ifndef ArtworkDir
  #error ArtworkDir is required
#endif

[Setup]
AppId=Sartiye.SignalPoint
AppName=SignalPoint
AppVersion={#AppVersion}
AppPublisher=Sartiye
AppPublisherURL=https://mb-cloudflare.ey.r.appspot.com/signalpoint/
AppSupportURL=https://mb-cloudflare.ey.r.appspot.com/signalpoint/
DefaultDirName={localappdata}\Programs\SignalPoint
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
WizardStyle=modern dark
SetupIconFile={#BundleDir}\icon.ico
WizardImageFile={#ArtworkDir}\wizard.bmp
WizardSmallImageFile={#ArtworkDir}\wizard-small.bmp
WizardImageStretch=no
WizardImageBackColor=$231111
UninstallDisplayIcon={app}\icon.ico
Compression=lzma2/normal
SolidCompression=yes
CloseApplications=yes
RestartApplications=no
OutputBaseFilename=SignalPoint-Setup-{#AppVersion}-x64

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\SignalPoint"; Filename: "{app}\SignalPoint.exe"; WorkingDir: "{app}"; IconFilename: "{app}\icon.ico"
Name: "{autodesktop}\SignalPoint"; Filename: "{app}\SignalPoint.exe"; WorkingDir: "{app}"; IconFilename: "{app}\icon.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\SignalPoint.exe"; Description: "Open SignalPoint"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent unchecked

; No UninstallDelete: user settings/cache and game modules live outside {app}.
