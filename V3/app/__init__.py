import logging
import os
import secrets
from logging.handlers import RotatingFileHandler

from flask import Flask, render_template

from . import db
from .version import APP_NAME, __version__


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or _load_or_create_secret_key()

    _configure_logging(app)
    app.teardown_appcontext(db.close_db)

    with app.app_context():
        db.init_db()

    from .hoy import bp as bp_hoy
    from .routes import bp

    app.register_blueprint(bp)
    app.register_blueprint(bp_hoy)
    _register_error_handlers(app)
    app.add_template_filter(formato_ar, "ar")
    app.add_template_filter(formato_pesos, "pesos")

    @app.context_processor
    def _identidad_app():
        return {"app_name": APP_NAME, "app_version": __version__}

    return app


def formato_ar(valor, decimales: int = 2) -> str:
    """Formatea un número al estilo argentino: punto de miles y coma decimal
    (ej. 3854969.5 -> "3.854.969,50")."""
    texto = f"{valor:,.{decimales}f}"
    return texto.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def formato_pesos(valor, decimales: int = 2) -> str:
    """Monto en pesos con el signo antes del $ (ej. -402.68 -> "−$402,68")."""
    signo = "−" if valor < 0 else ""
    return f"{signo}${formato_ar(abs(valor), decimales)}"


def _load_or_create_secret_key() -> str:
    """Genera una SECRET_KEY una sola vez y la persiste en instance/, para no
    depender de un valor hardcodeado ni invalidar sesiones en cada reinicio."""
    instance_dir = db.DB_PATH.parent
    instance_dir.mkdir(parents=True, exist_ok=True)
    key_path = instance_dir / "secret_key"
    if key_path.exists():
        return key_path.read_text().strip()

    key = secrets.token_hex(32)
    key_path.write_text(key)
    try:
        key_path.chmod(0o600)
    except OSError:
        pass
    return key


def _configure_logging(app):
    """Logueo a archivo rotativo: es la única forma de diagnosticar errores
    en el .exe empaquetado, que no tiene consola visible."""
    log_dir = db.DB_PATH.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    handler = RotatingFileHandler(
        log_dir / "app.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    )
    handler.setLevel(logging.INFO)

    # Se cuelga del logger raíz (no solo de app.logger) para que también
    # capture werkzeug y cualquier logger de desktop.py al empaquetar como
    # app de escritorio.
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)


def _register_error_handlers(app):
    @app.errorhandler(404)
    def not_found(error):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def server_error(error):
        app.logger.exception("Error interno no manejado")
        return render_template("errors/500.html"), 500
