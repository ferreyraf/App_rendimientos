"""Compila la app de escritorio y arma el instalador del sistema actual.

    python packaging/build.py

- Linux: dist/release/Bitacora-<versión>-x86_64.AppImage (se instala
  con `--instalar`) más instalar.sh / desinstalar.sh.
- Windows: dist/release/Bitacora-<versión>-Setup.exe (Inno Setup). Trae
  todo lo necesario (Python incluido); si a la PC le falta WebView2, el
  instalador lo agrega.

Requiere las dependencias de requirements.txt + requirements-build.txt.
"""

import os
import platform
import shutil
import stat
import subprocess
import sys
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PACKAGING = RAIZ / "packaging"
BUILD = RAIZ / "build"
DIST = RAIZ / "dist"
RELEASE = DIST / "release"

# La consola de Windows usa cp1252, que no tiene "→" ni "✔": sin esto el
# script se cae en el primer print.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(RAIZ))
from app.version import APP_ID, APP_NAME, DESCRIPTION, PUBLISHER, __version__  # noqa: E402

# Instalador "bootstrapper" de WebView2 (2 MB): baja e instala la versión
# actual. Solo se usa si a la PC le falta (Windows 10/11 ya lo traen).
WEBVIEW2_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
APPIMAGETOOL_URL = (
    "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
)


def _sep() -> str:
    return ";" if sys.platform == "win32" else ":"


def pyinstaller():
    """Compila en modo carpeta (arranca más rápido que un único archivo)."""
    datos = [
        ("app/static", "app/static"),
        ("app/templates", "app/templates"),
        ("packaging/icons/icono-256.png", "packaging/icons"),
    ]
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--windowed",
        "--name", APP_ID,
        "--distpath", str(DIST),
        "--workpath", str(BUILD / "pyinstaller"),
        "--specpath", str(BUILD),
    ]
    for origen, destino in datos:
        args += ["--add-data", f"{RAIZ / origen}{_sep()}{destino}"]
    if sys.platform == "win32":
        args += ["--icon", str(PACKAGING / "icons" / "icono.ico")]
        args += ["--version-file", str(_version_file_windows())]
        # pystray elige su backend en tiempo de ejecución: PyInstaller no lo ve solo.
        args += ["--hidden-import", "pystray._win32"]
    else:
        # Solo el backend Qt de pywebview; evita arrastrar GTK si está instalado.
        args += ["--exclude-module", "gi", "--exclude-module", "webview.platforms.gtk"]
    args.append(str(RAIZ / "desktop.py"))
    print("→ PyInstaller", __version__)
    subprocess.run(args, check=True, cwd=RAIZ)
    return DIST / APP_ID


def _version_file_windows() -> Path:
    """Metadatos que Windows muestra en Propiedades → Detalles del .exe."""
    partes = (__version__.split(".") + ["0", "0", "0"])[:4]
    tupla = ", ".join(str(int(p)) for p in partes)
    contenido = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({tupla}), prodvers=({tupla})),
  kids=[
    StringFileInfo([StringTable('040A04B0', [
      StringStruct('CompanyName', '{PUBLISHER}'),
      StringStruct('FileDescription', '{APP_NAME}'),
      StringStruct('FileVersion', '{__version__}'),
      StringStruct('InternalName', '{APP_ID}'),
      StringStruct('OriginalFilename', '{APP_ID}.exe'),
      StringStruct('ProductName', '{APP_NAME}'),
      StringStruct('ProductVersion', '{__version__}')])]),
    VarFileInfo([VarStruct('Translation', [0x040A, 1200])])
  ]
)
"""
    destino = BUILD / "version_info.txt"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(contenido, encoding="utf-8")
    return destino


# --- Linux: AppImage ---------------------------------------------------------

def _appimagetool() -> Path:
    herramienta = BUILD / "tools" / "appimagetool-x86_64.AppImage"
    if not herramienta.exists():
        herramienta.parent.mkdir(parents=True, exist_ok=True)
        print("→ Descargando appimagetool")
        urllib.request.urlretrieve(APPIMAGETOOL_URL, herramienta)
        herramienta.chmod(herramienta.stat().st_mode | stat.S_IEXEC)
    return herramienta


def _ejecutable(ruta: Path, contenido: str):
    ruta.write_text(contenido, encoding="utf-8")
    ruta.chmod(0o755)


def _podar_qt(compilado: Path):
    """PyInstaller copia todas las librerías de las wheels de Qt (3D,
    multimedia, sensores...). Deja solo las que necesitan los módulos y
    plugins empaquetados (siguiendo sus dependencias ELF), y las traducciones
    en español e inglés."""
    qt = compilado / "_internal" / "PyQt6" / "Qt6"
    lib = qt / "lib"
    if not lib.is_dir():
        return
    disponibles = {p.name: p for p in lib.iterdir()}

    def dependencias(binario: Path) -> list[str]:
        salida = subprocess.run(["readelf", "-d", str(binario)], capture_output=True, text=True).stdout
        return [linea.split("[", 1)[1].rstrip("]") for linea in salida.splitlines() if "(NEEDED)" in linea]

    semillas = list((qt.parent).glob("*.so")) + list((qt / "plugins").rglob("*.so")) + list((qt / "libexec").iterdir())
    necesarias, pendientes = set(), semillas
    while pendientes:
        binario = pendientes.pop()
        for nombre in dependencias(binario):
            if nombre in disponibles and nombre not in necesarias:
                necesarias.add(nombre)
                pendientes.append(disponibles[nombre])
    liberado = 0
    for nombre, ruta in disponibles.items():
        if nombre not in necesarias:
            liberado += ruta.stat().st_size if not ruta.is_symlink() else 0
            ruta.unlink()

    traducciones = qt / "translations"
    for archivo in traducciones.glob("*.qm"):
        if not archivo.stem.endswith(("_es", "_en")):
            liberado += archivo.stat().st_size
            archivo.unlink()
    for pak in (traducciones / "qtwebengine_locales").glob("*.pak"):
        if pak.stem not in ("es", "es-419", "en-US"):
            liberado += pak.stat().st_size
            pak.unlink()
    # PyInstaller deja symlinks a esas librerías en _internal/: los que quedaron
    # apuntando a nada se borran.
    for enlace in (compilado / "_internal").iterdir():
        if enlace.is_symlink() and not enlace.exists():
            enlace.unlink()
    print(f"→ Poda de Qt: {liberado / 1e6:.0f} MB menos")


def appimage(compilado: Path) -> Path:
    _podar_qt(compilado)
    appdir = BUILD / "AppDir"
    if appdir.exists():
        shutil.rmtree(appdir)
    destino_app = appdir / "usr" / "lib" / APP_ID
    shutil.copytree(compilado, destino_app, symlinks=True)

    linux = PACKAGING / "linux"
    for script in ("AppRun", "instalar.sh", "desinstalar.sh"):
        texto = (linux / script).read_text(encoding="utf-8")
        texto = texto.replace("@APP_ID@", APP_ID).replace("@APP_NAME@", APP_NAME).replace("@VERSION@", __version__)
        _ejecutable(appdir / script, texto)

    escritorio = (linux / "bitacora.desktop").read_text(encoding="utf-8")
    escritorio = escritorio.replace("@APP_NAME@", APP_NAME).replace("@DESCRIPTION@", DESCRIPTION)
    (appdir / "bitacora.desktop").write_text(escritorio.replace("@EXEC@", "AppRun"), encoding="utf-8")
    shutil.copy(PACKAGING / "icons" / "icono-256.png", appdir / "bitacora.png")
    shutil.copy(PACKAGING / "icons" / "icono-256.png", appdir / ".DirIcon")
    iconos = appdir / "usr" / "share" / "icons"
    for tamano in (32, 48, 64, 128, 256, 512):
        carpeta = iconos / "hicolor" / f"{tamano}x{tamano}" / "apps"
        carpeta.mkdir(parents=True, exist_ok=True)
        shutil.copy(PACKAGING / "icons" / f"icono-{tamano}.png", carpeta / "bitacora.png")

    RELEASE.mkdir(parents=True, exist_ok=True)
    salida = RELEASE / f"{APP_ID}-{__version__}-x86_64.AppImage"
    print("→ Armando", salida.name)
    entorno = dict(os.environ, ARCH="x86_64")
    subprocess.run(
        [str(_appimagetool()), "--appimage-extract-and-run", "--no-appstream", str(appdir), str(salida)],
        check=True, env=entorno,
    )

    # Atajos para quien prefiera un script: llaman a las opciones del AppImage.
    _ejecutable(RELEASE / "instalar.sh", f"""#!/bin/sh
# Instala {APP_NAME} {__version__} para este usuario (sin permisos de administrador).
cd "$(dirname "$0")" && exec ./{salida.name} --instalar
""")
    _ejecutable(RELEASE / "desinstalar.sh", f"""#!/bin/sh
# Desinstala {APP_NAME}. Tus datos quedan guardados salvo que agregues --borrar-datos.
exec "$HOME/.local/opt/{APP_ID}/{APP_ID}.AppImage" --desinstalar "$@"
""")
    return salida


# --- Windows: Inno Setup -----------------------------------------------------

def _iscc() -> Path | None:
    candidatos = [
        shutil.which("iscc"),
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe",
    ]
    for c in candidatos:
        if c and Path(c).exists():
            return Path(c)
    return None


def _webview2_bootstrapper() -> Path:
    destino = BUILD / "tools" / "MicrosoftEdgeWebview2Setup.exe"
    if not destino.exists():
        destino.parent.mkdir(parents=True, exist_ok=True)
        print("→ Descargando el instalador de WebView2")
        urllib.request.urlretrieve(WEBVIEW2_URL, destino)
    return destino


def instalador_windows(compilado: Path) -> Path:
    iscc = _iscc()
    if iscc is None:
        raise SystemExit("No se encontró Inno Setup 6 (ISCC.exe). Instalalo desde https://jrsoftware.org/isdl.php")
    RELEASE.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            str(iscc),
            f"/DWebView2Bootstrapper={_webview2_bootstrapper()}",
            f"/DAppVersion={__version__}",
            f"/DAppName={APP_NAME}",
            f"/DAppId={APP_ID}",
            f"/DPublisher={PUBLISHER}",
            f"/DSourceDir={compilado}",
            f"/DIconFile={PACKAGING / 'icons' / 'icono.ico'}",
            f"/DOutputDir={RELEASE}",
            str(PACKAGING / "windows" / "Bitacora.iss"),
        ],
        check=True,
    )
    return RELEASE / f"{APP_ID}-{__version__}-Setup.exe"


def main():
    print(f"{APP_NAME} {__version__} — {platform.system()} {platform.machine()}")
    compilado = pyinstaller()
    if sys.platform == "win32":
        salida = instalador_windows(compilado)
    elif sys.platform.startswith("linux"):
        salida = appimage(compilado)
    else:
        raise SystemExit(f"Plataforma no soportada para instalador: {sys.platform}")
    print("✔ Listo:", salida)


if __name__ == "__main__":
    main()
