; Instalador de Windows de Rulo Financiero (Inno Setup 6).
; packaging/build.py lo compila pasando la versión y las rutas con /D...;
; los valores de abajo son solo para compilarlo a mano.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef AppName
  #define AppName "Rulo Financiero"
#endif
#ifndef AppId
  #define AppId "RuloFinanciero"
#endif
#ifndef Publisher
  #define Publisher "Fer F"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\dist\RuloFinanciero"
#endif
#ifndef IconFile
  #define IconFile "..\icons\icono.ico"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\dist\release"
#endif

[Setup]
; GUID fijo: identifica la app entre versiones para que una nueva reemplace a la anterior.
AppId={{6E0B7C1A-3F4D-4B8E-9A21-5C7D2E9F1B30}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#Publisher}
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}
VersionInfoDescription=Instalador de {#AppName}
; Instalación por usuario: no pide permisos de administrador.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppId}.exe
SetupIconFile={#IconFile}
OutputDir={#OutputDir}
OutputBaseFilename={#AppId}-{#AppVersion}-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppId}.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppId}.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppId}.exe"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

; Los datos del usuario viven en %LOCALAPPDATA%\RuloFinanciero y no se borran
; al desinstalar, para no perder la información al actualizar o reinstalar.
