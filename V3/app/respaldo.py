"""Respaldo, restauración y sincronización de la base entre PCs.

La sincronización usa una carpeta compartida (ej. Google Drive para
escritorio): cada cambio publica una copia de la base en
`<carpeta>/Bitacora-sync.db`, y al abrir la app en otra PC, si esa copia
cambió, se trae. Siempre se hace un backup local antes de reemplazar datos.
"""

import hashlib
import logging
import platform
import shutil
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from . import db, preferencias
from .version import APP_ID, APP_NAME

logger = logging.getLogger(__name__)

# Propio de cada app (la V2 usa "RuloFinanciero-sync.db"): si las dos
# sincronizan en la misma carpeta, no se pisan.
ARCHIVO_SYNC = f"{APP_ID}-sync.db"
TABLAS_REQUERIDAS = {"aportes", "egresos", "ingresos", "wallets"}
REVISAR_CADA_SEGUNDOS = 120
_ultima_revision = 0.0


class RespaldoInvalido(ValueError):
    pass


def _hash(ruta: Path) -> str:
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


def validar(ruta: Path) -> None:
    """Lanza RespaldoInvalido si el archivo no es una base de la app (o de la V2)."""
    try:
        conn = sqlite3.connect(f"file:{ruta}?mode=ro", uri=True)
        try:
            tablas = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        raise RespaldoInvalido(f"El archivo no es un respaldo válido de {APP_NAME}.") from exc
    if not TABLAS_REQUERIDAS <= tablas:
        raise RespaldoInvalido(f"El archivo no es un respaldo de {APP_NAME} (le faltan datos).")


def exportar(destino: Path) -> Path:
    """Copia consistente de la base actual (aunque esté en uso) a `destino`."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    origen = sqlite3.connect(db.DB_PATH)
    copia = sqlite3.connect(destino)
    try:
        origen.backup(copia)
    finally:
        copia.close()
        origen.close()
    return destino


def backup_local(motivo: str) -> Path:
    """Copia de la base actual en la carpeta de backups antes de reemplazarla."""
    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    return exportar(db.BACKUP_DIR / f"rendimientos_{marca}_{motivo}.db")


def importar(origen: Path, motivo: str = "antes_de_importar") -> None:
    """Reemplaza el contenido de la base por el de `origen` (validado), dejando
    antes un backup. Usa la API de backup de SQLite, así no hay que cerrar las
    otras conexiones abiertas."""
    validar(origen)
    backup_local(motivo)
    fuente = sqlite3.connect(f"file:{origen}?mode=ro", uri=True)
    actual = sqlite3.connect(db.DB_PATH)
    try:
        fuente.backup(actual)
    finally:
        actual.close()
        fuente.close()
    # La base traída puede ser de una versión anterior: aplica las migraciones.
    db.init_db()


# --- Sincronización ----------------------------------------------------------

def carpeta_sync() -> Path | None:
    carpeta = preferencias.cargar()["sync_carpeta"].strip()
    return Path(carpeta).expanduser() if carpeta else None


def estado_sync() -> dict:
    prefs = preferencias.cargar()
    carpeta = carpeta_sync()
    archivo = carpeta / ARCHIVO_SYNC if carpeta else None
    return {
        "carpeta": prefs["sync_carpeta"],
        "activa": carpeta is not None,
        "carpeta_existe": bool(carpeta and carpeta.is_dir()),
        "archivo_existe": bool(archivo and archivo.exists()),
        "ultimo": prefs["sync_ultimo"],
        "pendiente": prefs["sync_pendiente"],
    }


def publicar() -> bool:
    """Deja una copia de la base en la carpeta compartida. Devuelve False si
    no se pudo (queda marcado como pendiente para el próximo intento)."""
    carpeta = carpeta_sync()
    if carpeta is None:
        return False
    try:
        if not carpeta.is_dir():
            raise OSError(f"No existe la carpeta {carpeta}")
        temporal = exportar(carpeta / (ARCHIVO_SYNC + f".{platform.node()}.tmp"))
        destino = carpeta / ARCHIVO_SYNC
        temporal.replace(destino)
        preferencias.guardar(
            sync_ultimo_hash=_hash(destino),
            sync_ultimo=datetime.now().isoformat(timespec="seconds"),
            sync_pendiente=False,
        )
        return True
    except OSError:
        logger.warning("No se pudo publicar la base en la carpeta de sincronización", exc_info=True)
        preferencias.guardar(sync_pendiente=True)
        return False


def traer_si_cambio(forzar: bool = False) -> str | None:
    """Si la copia de la carpeta compartida cambió desde la última vez (otra
    PC guardó algo), la trae. Devuelve un mensaje para mostrar, o None.

    Si además esta PC tenía cambios sin publicar, no se pierden: se guardan
    en la carpeta como "conflicto" y se avisa."""
    global _ultima_revision
    if not forzar and time.time() - _ultima_revision < REVISAR_CADA_SEGUNDOS:
        return None
    _ultima_revision = time.time()

    carpeta = carpeta_sync()
    if carpeta is None:
        return None
    remoto = carpeta / ARCHIVO_SYNC
    if not remoto.exists():
        return "publicada" if publicar() else None

    prefs = preferencias.cargar()
    try:
        hash_remoto = _hash(remoto)
    except OSError:
        logger.warning("No se pudo leer la base sincronizada", exc_info=True)
        return None
    if hash_remoto == prefs["sync_ultimo_hash"]:
        if prefs["sync_pendiente"]:
            publicar()
        return None

    mensaje = "Se trajeron los cambios hechos en tu otra PC."
    if prefs["sync_pendiente"]:
        marca = datetime.now().strftime("%Y%m%d_%H%M%S")
        conflicto = carpeta / f"{APP_ID}-conflicto-{platform.node()}-{marca}.db"
        exportar(conflicto)
        mensaje = (
            "Las dos PCs tenían cambios sin sincronizar: se trajeron los de la otra PC y los de esta "
            f"quedaron guardados en {conflicto.name}."
        )
    try:
        db.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        copia = Path(shutil.copy(remoto, db.BACKUP_DIR / "ultima_sync_entrante.db"))
        importar(copia, motivo="antes_de_sincronizar")
    except (OSError, RespaldoInvalido):
        logger.warning("No se pudo traer la base sincronizada", exc_info=True)
        return None
    preferencias.guardar(
        sync_ultimo_hash=hash_remoto,
        sync_ultimo=datetime.now().isoformat(timespec="seconds"),
        sync_pendiente=False,
    )
    return mensaje
