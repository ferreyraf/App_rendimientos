"""Punto de entrada de la app de escritorio: levanta el servidor Flask en un
puerto local libre y lo muestra en una ventana nativa (pywebview).

En Windows pywebview usa WebView2 (Edge); en Linux, Qt WebEngine.
"""

import logging
import socket
import sys
import threading
import time
from pathlib import Path

import webview

from app import create_app, db
from app.version import APP_NAME, __version__

HOST = "127.0.0.1"
# Puerto fijo solo para detectar si la app ya está abierta (no sirve nada).
PUERTO_INSTANCIA = 47391

logger = logging.getLogger(__name__)

_start_error: Exception | None = None


def _recurso(relativo: str) -> Path:
    """Ruta a un archivo empaquetado (PyInstaller lo extrae en _MEIPASS)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relativo


def _puerto_libre() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


def _tomar_instancia_unica() -> socket.socket | None:
    """Reserva el puerto de instancia mientras la app esté abierta. Devuelve
    None si otra ventana de la app ya lo tiene."""
    candado = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        candado.bind((HOST, PUERTO_INSTANCIA))
        candado.listen(1)
    except OSError:
        candado.close()
        return None
    return candado


def _run_flask(app, puerto):
    global _start_error
    try:
        app.run(host=HOST, port=puerto, debug=False, use_reloader=False, threaded=True)
    except Exception as exc:
        _start_error = exc
        logger.exception("No se pudo iniciar el servidor de %s", APP_NAME)


def _wait_for_server(puerto, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _start_error is not None:
            return False
        try:
            with socket.create_connection((HOST, puerto), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.05)
    return False


def _ventana_mensaje(titulo, cuerpo_html):
    webview.create_window(
        f"{APP_NAME} — {titulo}",
        html=(
            "<body style='font-family: system-ui, sans-serif; padding: 1.5rem 2rem; line-height: 1.4;'>"
            f"{cuerpo_html}</body>"
        ),
        width=620,
        height=320,
        resizable=False,
    )
    _start_webview()


def _start_webview():
    opciones = {}
    if sys.platform.startswith("linux"):
        opciones["gui"] = "qt"
        opciones["icon"] = str(_recurso("packaging/icons/icono-256.png"))
    webview.start(**opciones)


def main():
    candado = _tomar_instancia_unica()
    if candado is None:
        _ventana_mensaje(
            "Ya está abierta",
            f"<h2>{APP_NAME} ya está abierta</h2>"
            "<p>Hay otra ventana de la app funcionando. Usá esa; abrir dos a la vez "
            "podría pisar los datos.</p>",
        )
        return

    try:
        app = create_app()
    except Exception as exc:
        logger.exception("No se pudo crear la app")
        global _start_error
        _start_error = exc
        app = None

    puerto = _puerto_libre()
    if app is not None:
        threading.Thread(target=_run_flask, args=(app, puerto), daemon=True).start()

    if app is None or not _wait_for_server(puerto):
        detalle = f"<p><code>{_start_error}</code></p>" if _start_error else ""
        _ventana_mensaje(
            "Error",
            "<h2>No se pudo iniciar la aplicación</h2>"
            f"<p>Revisá el registro en <code>{db.DATA_DIR / 'logs' / 'app.log'}</code> para más detalle.</p>"
            f"{detalle}",
        )
        return

    logger.info("%s %s iniciada en el puerto %s", APP_NAME, __version__, puerto)
    webview.create_window(
        f"{APP_NAME} {__version__}",
        f"http://{HOST}:{puerto}",
        width=1320,
        height=900,
        min_size=(960, 640),
    )
    _start_webview()
    candado.close()


if __name__ == "__main__":
    main()
