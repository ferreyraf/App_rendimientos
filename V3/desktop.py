"""Punto de entrada de la app de escritorio: levanta el servidor Flask en un
puerto local libre y lo muestra en una ventana nativa (pywebview).

En Windows pywebview usa WebView2 (Edge); en Linux, Qt WebEngine.

Cerrar la ventana la deja funcionando en segundo plano (ícono en la bandeja),
para que sigan sonando las alarmas y llegando los avisos; "Salir" desde la
bandeja la cierra del todo. Abrir la app de nuevo vuelve a mostrar la misma
ventana. Con `--oculta` (el acceso directo de inicio de Windows) arranca
directo en la bandeja.
"""

import logging
import os
import socket
import sys
import threading
import time
from pathlib import Path

# El motor web de Qt arranca en inglés y muestra las fechas como mm/dd/aaaa;
# en español latinoamericano quedan dd/mm/aaaa. Tiene que estar antes de crear
# la ventana. (En Windows, WebView2 ya usa el idioma del sistema.)
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--lang=es-419")

import webview  # noqa: E402

from app import create_app, db, preferencias  # noqa: E402
from app.recordatorios import Recordatorios, VigiaAlarmas, notificar
from app.sonidos import Timbre  # noqa: E402
from app.version import APP_ID, APP_NAME, __version__  # noqa: E402

HOST = "127.0.0.1"
# Puerto fijo para detectar si la app ya está abierta y pedirle que se muestre.
PUERTO_INSTANCIA = 47393  # distinto del de la V2 (47391), para que convivan
MENSAJE_MOSTRAR = b"mostrar"

logger = logging.getLogger(__name__)

_start_error: Exception | None = None
# La aplicación Qt (Linux) tiene que vivir mientras dure la app: si se libera,
# Qt se cae al usarla (ej. al crear el ícono de la bandeja).
_app_qt = None


def _recurso(relativo: str) -> Path:
    """Ruta a un archivo empaquetado (PyInstaller lo extrae en _MEIPASS)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relativo


ICONO = str(_recurso("packaging/icons/icono-256.png"))


def _puerto_libre() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


def _tomar_instancia_unica() -> socket.socket | None:
    """Reserva el puerto de instancia mientras la app esté abierta. Devuelve
    None si otra instancia de la app ya lo tiene."""
    candado = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        candado.bind((HOST, PUERTO_INSTANCIA))
        candado.listen(4)
    except OSError:
        candado.close()
        return None
    return candado


def _pedir_que_se_muestre() -> bool:
    """Le pide a la instancia que ya está corriendo que muestre su ventana."""
    try:
        with socket.create_connection((HOST, PUERTO_INSTANCIA), timeout=2) as s:
            s.sendall(MENSAJE_MOSTRAR)
        return True
    except OSError:
        return False


def _escuchar_otras_instancias(candado: socket.socket, mostrar) -> None:
    while True:
        try:
            conexion, _ = candado.accept()
        except OSError:
            return
        with conexion:
            conexion.settimeout(2)
            try:
                if conexion.recv(32).startswith(MENSAJE_MOSTRAR):
                    mostrar()
            except OSError:
                pass


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


# --- Bandeja del sistema -------------------------------------------------------

class Bandeja:
    """Ícono en la bandeja con "Abrir" y "Salir". En Linux usa Qt (el mismo
    que dibuja la ventana); en Windows, pystray. Si el escritorio no tiene
    bandeja, `disponible` queda en False y cerrar la ventana cierra la app."""

    def __init__(self, abrir, salir):
        self.disponible = False
        self._abrir = abrir
        self._salir = salir
        try:
            if sys.platform.startswith("linux"):
                self._crear_qt()
            elif sys.platform == "win32":
                self._crear_pystray()
        except Exception:
            logger.warning("No se pudo crear el ícono de la bandeja", exc_info=True)

    def _crear_qt(self):
        from PyQt6.QtGui import QAction, QIcon
        from PyQt6.QtWidgets import QMenu, QSystemTrayIcon

        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self._icono = QSystemTrayIcon(QIcon(ICONO))
        self._icono.setToolTip(APP_NAME)
        self._menu = QMenu()
        abrir = QAction(f"Abrir {APP_NAME}", self._menu)
        abrir.triggered.connect(self._abrir)
        salir = QAction("Salir", self._menu)
        salir.triggered.connect(self._salir)
        self._menu.addAction(abrir)
        self._menu.addSeparator()
        self._menu.addAction(salir)
        self._icono.setContextMenu(self._menu)
        self._icono.activated.connect(
            lambda motivo: self._abrir() if motivo == QSystemTrayIcon.ActivationReason.Trigger else None
        )
        self._icono.show()
        self.disponible = True

    def _crear_pystray(self):
        import pystray
        from PIL import Image

        menu = pystray.Menu(
            pystray.MenuItem(f"Abrir {APP_NAME}", lambda: self._abrir(), default=True),
            pystray.MenuItem("Salir", lambda: self._salir()),
        )
        self._icono = pystray.Icon(APP_ID, Image.open(ICONO), APP_NAME, menu)
        self._icono.run_detached()
        self.disponible = True

    def ocultar(self):
        if not self.disponible:
            return
        if sys.platform.startswith("linux"):
            self._icono.hide()
        else:
            self._icono.stop()


def _preparar_qt():
    """Linux: crea la aplicación Qt antes que pywebview (que la reutiliza),
    para poder colgarle el ícono de la bandeja en el hilo principal."""
    global _app_qt
    import PyQt6.QtWebEngineWidgets  # noqa: F401  (tiene que importarse antes de crear la app)
    from PyQt6.QtWidgets import QApplication

    _app_qt = QApplication.instance() or QApplication(sys.argv)
    # Con la ventana escondida en segundo plano, cerrar el menú de la bandeja
    # no tiene que terminar la app: se sale solo con "Salir".
    _app_qt.setQuitOnLastWindowClosed(False)


def _terminar_qt():
    from PyQt6.QtCore import QMetaObject, Qt
    from PyQt6.QtWidgets import QApplication

    app_qt = QApplication.instance()
    if app_qt is not None:
        QMetaObject.invokeMethod(app_qt, "quit", Qt.ConnectionType.QueuedConnection)


# --- Ventanas -----------------------------------------------------------------

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


def _start_webview(al_arrancar=None):
    opciones = {}
    if sys.platform.startswith("linux"):
        opciones["gui"] = "qt"
        opciones["icon"] = ICONO
    webview.start(al_arrancar, **opciones)


def main():
    # Respaldos y exportaciones se descargan desde la propia ventana.
    webview.settings["ALLOW_DOWNLOADS"] = True

    candado = _tomar_instancia_unica()
    if candado is None:
        if _pedir_que_se_muestre():
            return
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
    if sys.platform.startswith("linux"):
        _preparar_qt()

    oculta = "--oculta" in sys.argv[1:]
    ventana = webview.create_window(
        f"{APP_NAME} {__version__}",
        f"http://{HOST}:{puerto}",
        width=1320,
        height=900,
        min_size=(960, 640),
        hidden=oculta,
    )
    estado = {"saliendo": False, "avisado": False}
    timbre = Timbre(db.DATA_DIR / "sonidos")

    def mostrar():
        ventana.show()
        ventana.restore()

    def salir():
        estado["saliendo"] = True
        timbre.detener()
        bandeja.ocultar()
        ventana.destroy()
        if sys.platform.startswith("linux"):
            _terminar_qt()

    bandeja = Bandeja(mostrar, salir)
    if oculta and not bandeja.disponible:
        # Sin bandeja no habría forma de abrirla: se muestra igual.
        threading.Thread(target=mostrar, daemon=True).start()

    def al_cerrar():
        if estado["saliendo"]:
            return True
        if bandeja.disponible and preferencias.cargar()["segundo_plano"]:
            # Se esconde desde otro hilo: hacerlo dentro del propio evento de
            # cierre puede trabar la ventana en algunos motores.
            threading.Thread(target=ventana.hide, daemon=True).start()
            if not estado["avisado"]:
                estado["avisado"] = True
                notificar(
                    f"{APP_NAME} sigue funcionando",
                    "Las alarmas y los avisos siguen andando. Abrila desde el ícono de la bandeja.",
                    ICONO,
                )
            return False
        estado["saliendo"] = True
        bandeja.ocultar()
        if sys.platform.startswith("linux"):
            ventana.events.closed += _terminar_qt
        return True

    ventana.events.closing += al_cerrar
    threading.Thread(target=_escuchar_otras_instancias, args=(candado, mostrar), daemon=True).start()
    Recordatorios(icono=ICONO).start()

    def al_sonar_alarma():
        """Trae la ventana al frente con el aviso de la alarma."""
        try:
            mostrar()
            ventana.on_top = True
            ventana.evaluate_js("window.revisarAlarmas && window.revisarAlarmas()")
            time.sleep(1)
            ventana.on_top = False
        except Exception:
            logger.warning("No se pudo mostrar la ventana de la alarma", exc_info=True)

    vigia = VigiaAlarmas(timbre, al_sonar=al_sonar_alarma, icono=ICONO)
    # La app toca el sonido de las alarmas (la página solo muestra el aviso),
    # y al detener o posponer se corta enseguida.
    app.config["SONIDO_NATIVO"] = True
    app.config["AL_ATENDER_ALARMA"] = vigia.revisar_ya
    # Al instalar otra versión desde Acerca de, la app se cierra sola.
    app.config["SALIR_APP"] = salir

    # El vigía arranca con la interfaz ya andando: si una alarma está sonando
    # al abrir, tiene que poder mostrar la ventana.
    _start_webview(vigia.start)
    candado.close()


if __name__ == "__main__":
    main()
