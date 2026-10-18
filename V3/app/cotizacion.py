"""Cotización del dólar para los gastos/ingresos cargados en dólares.

Fuente: precio de venta del "Dólar Blue en Córdoba" que publica infodolar.com.
Se guarda en la base (tabla `cotizaciones`) para que la app funcione sin
conexión con el último valor conocido, y se refresca sola cuando queda vieja.
"""

import html
import logging
import re
import time
import urllib.request
from datetime import datetime

from . import db

logger = logging.getLogger(__name__)

INFODOLAR_URL = "https://www.infodolar.com/cotizacion-dolar-provincia-cordoba.aspx"
FUENTE = "Dólar Blue en Córdoba (infodolar.com, venta)"
VIGENCIA_SEGUNDOS = 6 * 3600
# Tras un intento fallido (ej. sin conexión) no se reintenta enseguida, para
# no demorar cada página esperando el timeout.
REINTENTO_SEGUNDOS = 30 * 60
_ultimo_intento = 0.0


def _numero_ar(texto: str) -> float:
    """"$ 1.571,00" -> 1571.0"""
    return float(texto.replace("$", "").replace(".", "").replace(",", ".").strip())


def parsear_blue_cordoba(pagina: str) -> tuple[float, datetime]:
    """(precio de venta, fecha de publicación) de la tabla "Dólar Blue en
    Córdoba" de infodolar. Lanza ValueError si la página cambió de formato."""
    tabla = re.search(r'<table[^>]*id="BluePromedio".*?</table>', pagina, re.S)
    if not tabla:
        raise ValueError("No se encontró la tabla del dólar blue en Córdoba.")
    precios = re.findall(r'class="colCompraVenta"\s+data-order="([^"]+)"', tabla.group(0))
    fecha = re.search(r'class="timeago date"\s+title="([^"]+)"', tabla.group(0))
    if len(precios) < 2 or not fecha:
        raise ValueError("La tabla del dólar blue en Córdoba no tiene compra/venta/fecha.")
    venta = _numero_ar(html.unescape(precios[1]))
    if venta <= 0:
        raise ValueError("Precio de venta inválido.")
    return venta, datetime.fromisoformat(fecha.group(1))


def consultar_infodolar() -> tuple[float, datetime]:
    pedido = urllib.request.Request(INFODOLAR_URL, headers={"User-Agent": "Mozilla/5.0 Bitacora/3.0"})
    with urllib.request.urlopen(pedido, timeout=8) as resp:
        return parsear_blue_cordoba(resp.read().decode("utf-8", errors="replace"))


def actualizar(conn) -> dict | None:
    """Consulta infodolar y guarda el valor. Devuelve la cotización guardada,
    o None si no se pudo consultar (queda el último valor conocido)."""
    global _ultimo_intento
    _ultimo_intento = time.time()
    try:
        venta, publicado = consultar_infodolar()
    except (OSError, ValueError):
        logger.warning("No se pudo actualizar la cotización del dólar", exc_info=True)
        return None
    db.guardar_cotizacion(conn, "USD", venta, FUENTE, publicado)
    return db.get_cotizacion(conn, "USD")


def refrescar_si_vieja(conn) -> None:
    """Actualiza la cotización si hay movimientos en dólares y el valor
    guardado tiene más de unas horas (o no hay ninguno)."""
    if not db.hay_movimientos_en("USD", conn):
        return
    if time.time() - _ultimo_intento < REINTENTO_SEGUNDOS:
        return
    actual = db.get_cotizacion(conn, "USD")
    if actual and (datetime.now() - actual["actualizado"]).total_seconds() < VIGENCIA_SEGUNDOS:
        return
    actualizar(conn)
