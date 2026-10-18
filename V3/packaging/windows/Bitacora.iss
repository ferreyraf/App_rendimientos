; Instalador de Windows de Bitácora (Inno Setup 6).
; packaging/build.py lo compila pasando la versión y las rutas con /D...;
; los valores de abajo son solo para compilarlo a mano.
;
; Todo lo que la app necesita va adentro (Python y sus librerías, compiladas
; con PyInstaller). Lo único externo es WebView2, el motor de Edge con el
; que se dibuja la ventana: Windows 10/11 ya lo traen, y si falta el
; instalador lo agrega solo.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef AppName
  #define AppName "Bitácora"
#endif
#ifndef AppId
  #define AppId "Bitacora"
#endif
#ifndef Publisher
  #define Publisher "Fer F"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\dist\Bitacora"
#endif
#ifndef IconFile
  #define IconFile "..\icons\icono.ico"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\dist\release"
#endif

[Setup]
; GUID fijo: identifica la app entre versiones para que una nueva reemplace a
; la anterior. Distinto del de la V2 (Rulo Financiero): las dos conviven.
AppId={{C18C0683-64D4-49B7-ABF1-1549B3DD49BA}
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
DefaultDirName={autopf}\{#AppId}
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
MinVersion=10.0
CloseApplications=yes

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "inicio"; Description: "Abrir {#AppName} al iniciar Windows, en segundo plano (para que suenen las alarmas y lleguen los avisos)"; GroupDescription: "Alarmas y avisos:"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
#ifdef WebView2Bootstrapper
Source: "{#WebView2Bootstrapper}"; DestDir: "{tmp}"; DestName: "MicrosoftEdgeWebview2Setup.exe"; Flags: deleteafterinstall; Check: FaltaWebView2
#endif

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppId}.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppId}.exe"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppId}.exe"; Parameters: "--oculta"; Tasks: inicio

[Run]
#ifdef WebView2Bootstrapper
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; StatusMsg: "Instalando Microsoft Edge WebView2 (el motor de la ventana)..."; Flags: waituntilterminated; Check: FaltaWebView2
#endif
Filename: "{app}\{#AppId}.exe"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

; Los datos del usuario viven en %LOCALAPPDATA%\Bitacora y no se borran al
; desinstalar, para no perder la información al actualizar o reinstalar.

[Code]
// WebView2 está instalado si su versión ("pv") figura en el registro, para
// toda la máquina o para el usuario. Ver
// https://learn.microsoft.com/microsoft-edge/webview2/concepts/distribution
const
  ClaveWebView2 = 'Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';

function VersionValida(Raiz: Integer; Clave: String): Boolean;
var
  Version: String;
begin
  Result := RegQueryStringValue(Raiz, Clave, 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0');
end;

function FaltaWebView2: Boolean;
begin
  Result := not (VersionValida(HKLM32, 'SOFTWARE\' + ClaveWebView2)
    or VersionValida(HKLM64, 'SOFTWARE\' + ClaveWebView2)
    or VersionValida(HKCU, 'Software\' + ClaveWebView2));
end;
