"""Preferencias de este equipo (avisos, sincronización, segundo plano).

Van en un JSON aparte de la base a propósito: la base se sincroniza entre
PCs, y estas preferencias son de cada una (ej. la carpeta de Google Drive
tiene otra ruta en Windows que en Linux).
"""

import json
import logging

from . import db

logger = logging.getLogger(__name__)

POR_DEFECTO = {
    # Avisos del rulo: minutos antes de cada captura (0 = solo en el momento).
    "avisos_rulo": True,
    "avisos_minutos": 10,
    # Aviso a la mañana con los pagos que vencen ese día.
    "avisos_pagos": True,
    # Avisos de los bloques de la agenda (cada bloque elige cuánto antes).
    "avisos_agenda": True,
    # Cerrar la ventana deja la app funcionando (para que lleguen los avisos).
    "segundo_plano": True,
    # Sincronización entre PCs vía una carpeta compartida (ej. Google Drive).
    "sync_carpeta": "",
    "sync_ultimo_hash": "",
    "sync_ultimo": "",
    "sync_pendiente": False,
}


def _ruta():
    return db.DATA_DIR / "preferencias.json"


def cargar() -> dict:
    datos = dict(POR_DEFECTO)
    try:
        datos.update(json.loads(_ruta().read_text(encoding="utf-8")))
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        logger.warning("No se pudieron leer las preferencias; se usan las de fábrica", exc_info=True)
    return datos


def guardar(**cambios) -> dict:
    datos = cargar()
    datos.update(cambios)
    ruta = _ruta()
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_suffix(".tmp")
    temporal.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
    temporal.replace(ruta)
    return datos
