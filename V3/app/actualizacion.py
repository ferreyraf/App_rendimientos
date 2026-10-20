"""Actualizaciones de la app instalada y recuperación de datos.

- Instalar otra versión (más nueva o anterior) publicada en GitHub: se
  descarga el instalador de este sistema, opcionalmente se respaldan los
  datos antes, y al cerrarse la app se instala y se vuelve a abrir sola.
- En Linux, antes de reemplazar el AppImage se guarda una copia del
  instalado, para poder volver a esa versión sin internet.
- Recuperar datos: restaurar cualquiera de los backups de la base.

Solo la app empaquetada se actualiza a sí misma; en desarrollo (o la web
servida con run.py) se actualiza con git.
"""

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from flask import Blueprint, current_app, flash, jsonify, redirect, request, url_for

from . import db, novedades, respaldo
from .routes import _como_tupla
from .version import APP_ID, PREFIJO_TAG, REPO_GITHUB, __version__

logger = logging.getLogger(__name__)

bp = Blueprint("actualizacion", __name__, url_prefix="/actualizacion")

CACHE_SEGUNDOS = 3600
MAX_RESPALDOS = 5
# Lo que lleva el nombre del instalador de cada sistema en la Release.
SUFIJOS = {"linux": "-x86_64.AppImage", "windows": "-Setup.exe"}
MOTIVOS = {
    None: "Automático (al abrir la app o al cambiar de datos)",
    "antes_de_importar": "Antes de restaurar un respaldo",
    "antes_de_restaurar": "Antes de restaurar un respaldo",
    "antes_de_recuperar": "Antes de recuperar otro respaldo",
    "antes_de_sincronizar": "Antes de traer los datos de otra PC",
}
_PATRON_RESPALDO = re.compile(r"rendimientos_(\d{8}_\d{6})(?:_(.+))?\.db")

_cache: dict = {"timestamp": 0.0, "releases": None}
_estado_lock = threading.Lock()
_estado: dict = {"fase": "inactivo"}


def carpeta() -> Path:
    return db.DATA_DIR / "actualizaciones"


def carpeta_respaldos() -> Path:
    """Respaldos de antes de instalar otra versión: aparte de los automáticos,
    así la limpieza de esos (quedan los últimos 10) no los borra."""
    return db.BACKUP_DIR / "actualizaciones"


def plataforma() -> str | None:
    """Sistema en el que esta app se puede actualizar sola, o None."""
    if not getattr(sys, "frozen", False):
        return None
    if sys.platform == "win32":
        return "windows"
    if sys.platform.startswith("linux") and os.environ.get("APPIMAGE"):
        return "linux"
    return None


def motivo_sin_actualizar() -> str | None:
    if plataforma():
        return None
    if not getattr(sys, "frozen", False):
        return "Estás usando la versión de desarrollo: se actualiza con git. Desde acá podés ver las versiones y recuperar datos."
    return "Esta copia de la app no se puede actualizar sola: instalá la versión nueva a mano."


# --- Versiones publicadas ------------------------------------------------------

def _releases() -> list[dict]:
    """Releases de Bitácora publicadas, de la más nueva a la más vieja."""
    ahora = time.time()
    if _cache["releases"] is not None and ahora - _cache["timestamp"] < CACHE_SEGUNDOS:
        return _cache["releases"]
    pedido = urllib.request.Request(
        f"https://api.github.com/repos/{REPO_GITHUB}/releases?per_page=50",
        headers={"User-Agent": f"{APP_ID}/{__version__}", "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(pedido, timeout=10) as resp:
        crudas = json.load(resp)
    releases = []
    for r in crudas:
        tupla = _como_tupla(r.get("tag_name", ""))
        if r.get("draft") or r.get("prerelease") or not tupla or not r.get("tag_name", "").startswith(PREFIJO_TAG):
            continue
        releases.append({
            "version": r["tag_name"].removeprefix(PREFIJO_TAG),
            "tupla": tupla,
            "fecha": (r.get("published_at") or "")[:10],
            "url": r.get("html_url", ""),
            "notas": r.get("body") or "",
            "instaladores": {
                sistema: {"nombre": a["name"], "url": a["browser_download_url"], "tamano": a["size"]}
                for a in r.get("assets", [])
                for sistema, sufijo in SUFIJOS.items() if a["name"].endswith(sufijo)
            },
        })
    releases.sort(key=lambda r: r["tupla"], reverse=True)
    _cache.update(timestamp=ahora, releases=releases)
    return releases


def _cambios(version: str, notas: str) -> tuple[str, list[str]]:
    """Título y lista de cambios: los de novedades.py o, para versiones que
    esta todavía no conoce, los de la Release."""
    propia = novedades.de_version(version)
    if propia:
        return propia["titulo"], list(propia["cambios"])
    titulo, cambios = "", []
    for linea in notas.splitlines():
        linea = linea.strip()
        if linea.startswith("## ") and not titulo:
            titulo = linea[3:].strip()
        elif linea.startswith(("- ", "* ")):
            cambios.append(linea[2:].strip())
    return titulo, cambios


def version_guardada() -> tuple[str, Path] | None:
    """(Linux) El AppImage de la versión anterior que quedó guardado."""
    for archivo in sorted((carpeta() / "anterior").glob(f"{APP_ID}-*.AppImage")):
        version = archivo.name.removeprefix(f"{APP_ID}-").removesuffix(".AppImage")
        if _como_tupla(version) and version != __version__:
            return version, archivo
    return None


@bp.route("/versiones")
def versiones():
    sistema = plataforma()
    guardada = version_guardada() if sistema == "linux" else None
    respuesta = {
        "actual": __version__,
        "motivo": motivo_sin_actualizar(),
        "guardada": guardada[0] if guardada else None,
        "versiones": [],
        "error": None,
    }
    try:
        releases = _releases()
    except (OSError, ValueError, KeyError, TypeError):
        logger.info("No se pudieron consultar las versiones publicadas", exc_info=True)
        respuesta["error"] = "No se pudo consultar GitHub (¿sin internet?)."
        releases = []
    actual = _como_tupla(__version__)
    for r in releases:
        titulo, cambios = _cambios(r["version"], r["notas"])
        instalador = r["instaladores"].get(sistema) if sistema else None
        respuesta["versiones"].append({
            "version": r["version"],
            "fecha": (novedades.de_version(r["version"]) or r)["fecha"],
            "url": r["url"],
            "titulo": titulo,
            "cambios": cambios,
            "es_nueva": r["tupla"] > actual,
            "es_actual": r["tupla"] == actual,
            "instalable": bool(instalador) and r["tupla"] != actual,
            "tamano_mb": round(instalador["tamano"] / 1e6) if instalador else None,
        })
    return jsonify(respuesta)


# --- Instalar ----------------------------------------------------------------------

def _poner_estado(**valores) -> None:
    with _estado_lock:
        _estado.clear()
        _estado.update(valores)


def _respaldar(version: str) -> Path:
    carpeta_respaldos().mkdir(parents=True, exist_ok=True)
    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    destino = respaldo.exportar(carpeta_respaldos() / f"rendimientos_{marca}_antes_de_{version}.db")
    for viejo in sorted(carpeta_respaldos().glob("rendimientos_*.db"))[:-MAX_RESPALDOS]:
        viejo.unlink()
    return destino


def _descargar(url: str, destino: Path, tamano: int, version: str) -> None:
    parcial = destino.with_name(destino.name + ".parte")
    pedido = urllib.request.Request(url, headers={"User-Agent": f"{APP_ID}/{__version__}"})
    bajado = 0
    with urllib.request.urlopen(pedido, timeout=30) as resp, open(parcial, "wb") as salida:
        while bloque := resp.read(1 << 20):
            salida.write(bloque)
            bajado += len(bloque)
            _poner_estado(fase="descargando", version=version, progreso=round(bajado * 100 / tamano) if tamano else None)
    if tamano and bajado != tamano:
        parcial.unlink(missing_ok=True)
        raise OSError(f"La descarga quedó incompleta ({bajado} de {tamano} bytes).")
    parcial.replace(destino)


def _entorno_limpio() -> dict:
    """El entorno sin lo que agregan el AppImage y PyInstaller, para que el
    instalador nuevo no herede las librerías de este."""
    entorno = {k: v for k, v in os.environ.items()
               if k not in ("APPIMAGE", "APPDIR", "ARGV0", "OWD") and not k.startswith(("QT_", "QTWEBENGINE", "PYTHON"))}
    if "LD_LIBRARY_PATH_ORIG" in entorno:
        entorno["LD_LIBRARY_PATH"] = entorno.pop("LD_LIBRARY_PATH_ORIG")
    else:
        entorno.pop("LD_LIBRARY_PATH", None)
    return entorno


def _lanzar_instalador(instalador: Path) -> None:
    """Cuando esta app se cierra, instala `instalador` y la vuelve a abrir."""
    if plataforma() == "linux":
        instalador.chmod(0o755)
        script = ('while kill -0 "$1" 2>/dev/null; do sleep 0.5; done; '
                  '"$2" --instalar >/dev/null 2>&1; exec "$HOME/.local/bin/bitacora"')
        subprocess.Popen(
            ["/bin/sh", "-c", script, "sh", str(os.getpid()), str(instalador)],
            env=_entorno_limpio(), start_new_session=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    else:
        # cmd espera ~3 s a que la app se cierre (ping es lo único que
        # funciona sin consola), corre el instalador sin preguntas y la abre.
        comando = (f'ping -n 4 127.0.0.1 >nul & "{instalador}" /SILENT /SUPPRESSMSGBOXES /NORESTART '
                   f'/CLOSEAPPLICATIONS & start "" "{sys.executable}"')
        subprocess.Popen(
            f'cmd.exe /c "{comando}"',
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )


def _guardar_instalada(version_nueva: str) -> None:
    """(Linux) Copia el AppImage en uso a actualizaciones/anterior/ (solo
    queda esa), para poder volver a esta versión sin internet."""
    actual = Path(os.environ["APPIMAGE"])
    anterior = carpeta() / "anterior"
    anterior.mkdir(parents=True, exist_ok=True)
    destino = anterior / f"{APP_ID}-{__version__}.AppImage"
    if not destino.exists():
        temporal = destino.with_name(destino.name + ".parte")
        shutil.copy2(actual, temporal)
        temporal.replace(destino)
    for viejo in anterior.glob(f"{APP_ID}-*.AppImage"):
        if viejo != destino:
            viejo.unlink()


def _instalar(salir, version: str, origen: dict, respaldar: bool) -> None:
    try:
        sistema = plataforma()
        if respaldar:
            _poner_estado(fase="respaldando", version=version)
            _respaldar(version)
        descargas = carpeta() / "descargas"
        descargas.mkdir(parents=True, exist_ok=True)
        if origen.get("local"):
            # La versión guardada pasa a descargas: en anterior/ va a quedar
            # la que se está reemplazando.
            instalador = descargas / Path(origen["local"]).name
            Path(origen["local"]).replace(instalador)
        else:
            instalador = descargas / origen["nombre"]
            _descargar(origen["url"], instalador, origen["tamano"], version)
        for viejo in descargas.iterdir():
            if viejo != instalador:
                viejo.unlink()
        _poner_estado(fase="instalando", version=version)
        if sistema == "linux":
            _guardar_instalada(version)
        _lanzar_instalador(instalador)
    except Exception as exc:  # noqa: BLE001 — cualquier falla se informa en pantalla
        logger.exception("No se pudo instalar la versión %s", version)
        _poner_estado(fase="error", version=version, mensaje=f"No se pudo instalar la {version}: {exc}")
        return
    logger.info("Instalando la versión %s: la app se reinicia", version)
    _poner_estado(fase="reiniciando", version=version)
    if salir:
        threading.Timer(1.5, salir).start()


@bp.route("/instalar", methods=["POST"])
def instalar():
    motivo = motivo_sin_actualizar()
    if motivo:
        return jsonify({"error": motivo}), 400
    with _estado_lock:
        if _estado.get("fase") in ("respaldando", "descargando", "instalando", "reiniciando"):
            return jsonify({"error": "Ya hay una instalación en curso."}), 409
    version = (request.form.get("version") or "").strip()
    if version == __version__:
        return jsonify({"error": f"Ya tenés la {version}."}), 400
    guardada = version_guardada() if plataforma() == "linux" else None
    if guardada and guardada[0] == version and request.form.get("origen") == "local":
        origen = {"local": str(guardada[1])}
    else:
        try:
            release = next((r for r in _releases() if r["version"] == version), None)
        except (OSError, ValueError, KeyError, TypeError):
            return jsonify({"error": "No se pudo consultar GitHub (¿sin internet?)."}), 502
        origen = release["instaladores"].get(plataforma()) if release else None
        if origen is None:
            return jsonify({"error": f"La versión {version} no tiene instalador para este sistema."}), 400
    _poner_estado(fase="respaldando", version=version)
    threading.Thread(
        target=_instalar,
        args=(current_app.config.get("SALIR_APP"), version, origen, request.form.get("respaldar") == "1"),
        daemon=True,
    ).start()
    return jsonify({"ok": True})


@bp.route("/estado")
def estado():
    with _estado_lock:
        return jsonify(dict(_estado))


# --- Recuperar datos -----------------------------------------------------------------

def listar_respaldos() -> list[dict]:
    """Backups de la base, del más nuevo al más viejo. `id` es la ruta
    relativa a la carpeta de backups (lo que se manda para restaurar)."""
    lista = []
    for archivo in list(db.BACKUP_DIR.glob("rendimientos_*.db")) + list(carpeta_respaldos().glob("rendimientos_*.db")):
        coincide = _PATRON_RESPALDO.fullmatch(archivo.name)
        if not coincide:
            continue
        motivo = coincide.group(2)
        if motivo and motivo.startswith("antes_de_") and _como_tupla(motivo.removeprefix("antes_de_")):
            texto = f"Antes de instalar la {motivo.removeprefix('antes_de_')}"
        else:
            texto = MOTIVOS.get(motivo, motivo.replace("_", " ").capitalize() if motivo else MOTIVOS[None])
        lista.append({
            "id": archivo.relative_to(db.BACKUP_DIR).as_posix(),
            "fecha": datetime.strptime(coincide.group(1), "%Y%m%d_%H%M%S"),
            "motivo": texto,
            "tamano_kb": archivo.stat().st_size / 1024,
        })
    return sorted(lista, key=lambda r: r["fecha"], reverse=True)


@bp.route("/respaldos/restaurar", methods=["POST"])
def restaurar():
    volver = url_for("main.acerca", _anchor="recuperar")
    elegido = request.form.get("id") or ""
    if elegido not in {r["id"] for r in listar_respaldos()}:
        flash("Ese respaldo ya no existe.", "error")
        return redirect(volver)
    try:
        respaldo.importar(db.BACKUP_DIR / elegido, motivo="antes_de_recuperar")
    except respaldo.RespaldoInvalido as exc:
        flash(str(exc), "error")
        return redirect(volver)
    if respaldo.carpeta_sync() is not None:
        respaldo.publicar()
    flash("Datos recuperados. Lo que tenías hasta recién quedó guardado como respaldo, por si querés volver.", "success")
    return redirect(volver)
