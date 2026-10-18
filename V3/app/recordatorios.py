"""Recordatorios como notificaciones del sistema.

Corre en un hilo de la app de escritorio: unos minutos antes de cada
captura avisa a qué billetera mover la plata (y otra vez a la hora justa), a
la mañana avisa los pagos que vencen ese día, y avisa los bloques de la
agenda según el aviso de cada uno. Las preferencias (activar,
minutos de anticipación) están en `preferencias`.
"""

import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import date, datetime, timedelta

from . import db, preferencias
from .domain import _parse_hhmm, billetera_actual
from .version import APP_NAME

logger = logging.getLogger(__name__)

HORA_AVISO_PAGOS = 9  # a partir de las 9 de la mañana


def _entorno_sistema() -> dict:
    """Entorno para lanzar programas del sistema. Empaquetada con PyInstaller,
    la app agrega sus propias librerías a LD_LIBRARY_PATH; los programas del
    sistema (notify-send) tienen que correr con las del sistema."""
    entorno = dict(os.environ)
    if "LD_LIBRARY_PATH_ORIG" in entorno:
        entorno["LD_LIBRARY_PATH"] = entorno["LD_LIBRARY_PATH_ORIG"]
    elif getattr(sys, "frozen", False):
        entorno.pop("LD_LIBRARY_PATH", None)
    return entorno


_TOAST_WINDOWS = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
$plantilla = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$textos = $plantilla.GetElementsByTagName('text')
$textos.Item(0).AppendChild($plantilla.CreateTextNode($env:RULO_TITULO)) > $null
$textos.Item(1).AppendChild($plantilla.CreateTextNode($env:RULO_CUERPO)) > $null
$aviso = [Windows.UI.Notifications.ToastNotification]::new($plantilla)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe').Show($aviso)
"""


def notificar(titulo: str, cuerpo: str, icono: str | None = None) -> bool:
    """Muestra una notificación del sistema. Devuelve False si no se pudo."""
    try:
        if sys.platform.startswith("linux"):
            programa = shutil.which("notify-send")
            if not programa:
                logger.info("Sin notify-send: no se puede mostrar el aviso %r", titulo)
                return False
            comando = [programa, "-a", APP_NAME, titulo, cuerpo]
            if icono:
                comando[1:1] = ["-i", icono]
            subprocess.run(comando, env=_entorno_sistema(), timeout=10, check=False)
            return True
        if sys.platform == "win32":
            entorno = dict(os.environ, RULO_TITULO=titulo, RULO_CUERPO=cuerpo)
            subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", _TOAST_WINDOWS],
                env=entorno, timeout=20, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return True
        if sys.platform == "darwin":
            script = f'display notification "{cuerpo}" with title "{titulo}"'
            subprocess.run(["osascript", "-e", script], timeout=10, check=False)
            return True
    except (OSError, subprocess.SubprocessError):
        logger.warning("No se pudo mostrar la notificación", exc_info=True)
    return False


def _conectar():
    conn = sqlite3.connect(db.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _capturas_de_hoy(wallets, ahora: datetime) -> list[tuple[datetime, list]]:
    """Horarios de captura de hoy, cada uno con sus billeteras (puede haber
    varias a la misma hora)."""
    hoy = ahora.date()
    por_hora: dict[datetime, list] = {}
    for w in wallets:
        if w.activo and hoy.weekday() in w.active_weekdays:
            por_hora.setdefault(datetime.combine(hoy, _parse_hhmm(w.capture_time)), []).append(w)
    return sorted(por_hora.items(), key=lambda par: par[0])


def avisos_pendientes(ahora: datetime, wallets, pagos_de_hoy, prefs: dict, enviados: set) -> list[tuple[str, str, str]]:
    """Qué notificar en este momento: lista de (clave, título, cuerpo). Una
    clave ya enviada no se repite. Separado del hilo para poder probarlo."""
    avisos = []
    if prefs["avisos_rulo"]:
        minutos = int(prefs["avisos_minutos"] or 0)
        for momento, destino in _capturas_de_hoy(wallets, ahora):
            nombres = " o ".join(w.name for w in destino)
            antes = momento - timedelta(minutes=minutos)
            actual = billetera_actual(momento - timedelta(seconds=1), wallets)
            desde = f" desde {actual[1].name}" if actual and actual[1] not in destino else ""
            if minutos and antes <= ahora < momento:
                avisos.append((
                    f"previo-{momento:%Y%m%d%H%M}",
                    f"En {minutos} min: pasá la plata a {nombres}",
                    f"Captura a las {momento:%H:%M}. Movela{desde} antes de esa hora.",
                ))
            if momento <= ahora < momento + timedelta(minutes=3):
                avisos.append((
                    f"ahora-{momento:%Y%m%d%H%M}",
                    f"Ahora: la plata tiene que estar en {nombres}",
                    f"Captura de las {momento:%H:%M}.",
                ))
    if prefs["avisos_pagos"] and ahora.hour >= HORA_AVISO_PAGOS and pagos_de_hoy:
        detalle = ", ".join(f"{p['etiqueta']} ${p['monto']:,.0f}".replace(",", ".") for p in pagos_de_hoy[:4])
        if len(pagos_de_hoy) > 4:
            detalle += f" y {len(pagos_de_hoy) - 4} más"
        avisos.append((
            f"pagos-{ahora:%Y%m%d}",
            f"Hoy vence{'n' if len(pagos_de_hoy) != 1 else ''} {len(pagos_de_hoy)} pago{'s' if len(pagos_de_hoy) != 1 else ''}",
            detalle + ". Marcalos en Este mes cuando los hagas.",
        ))
    return [a for a in avisos if a[0] not in enviados]


class Recordatorios(threading.Thread):
    """Revisa cada 20 segundos si hay algo para avisar."""

    def _avisos_agenda(self, ahora: datetime) -> list[tuple[str, str, str]]:
        """Bloques de la agenda de hoy que tienen que avisar ahora. La app de
        escritorio es de una persona: usa el usuario local."""
        from .rutina import datos as datos_rutina
        from .rutina.logica import avisos_agenda, bloques_del_dia

        conn = _conectar()
        try:
            bloques = datos_rutina.get_bloques(conn, db.usuario_actual())
        finally:
            conn.close()
        return avisos_agenda(bloques_del_dia(bloques, ahora.date()), ahora, self.enviados)

    def __init__(self, icono: str | None = None):
        super().__init__(daemon=True, name="recordatorios")
        self.icono = icono
        self.enviados: set[str] = set()

    def run(self):
        # Import tardío: routes arma los objetos de dominio a partir de la base.
        from .routes import _ocurrencias_mes, _wallet_from_row

        while True:
            try:
                prefs = preferencias.cargar()
                if prefs["avisos_rulo"] or prefs["avisos_pagos"] or prefs["avisos_agenda"]:
                    ahora = datetime.now()
                    conn = _conectar()
                    try:
                        wallets = [_wallet_from_row(r) for r in db.get_wallets(conn)]
                        hoy = date.today()
                        pagos = [
                            {"etiqueta": f["etiqueta"], "monto": f["estimado"]}
                            for f in _ocurrencias_mes(conn, hoy.year, hoy.month, hoy)
                            if f["tipo"] == "egreso" and f["fecha"] == hoy and f["real"] is None
                        ]
                    finally:
                        conn.close()
                    pendientes = avisos_pendientes(ahora, wallets, pagos, prefs, self.enviados)
                    if prefs["avisos_agenda"]:
                        pendientes += self._avisos_agenda(ahora)
                    for clave, titulo, cuerpo in pendientes:
                        self.enviados.add(clave)
                        notificar(titulo, cuerpo, self.icono)
            except Exception:
                logger.exception("Error revisando recordatorios")
            time.sleep(20)
