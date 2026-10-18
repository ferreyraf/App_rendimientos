"""Contraseña de acceso para la versión web servida a la red (`run.py`).

Se activa con `app.config["ACCESO_CON_CLAVE"]`; la app de escritorio no la
usa (solo escucha en 127.0.0.1 dentro de la misma PC). El hash de la
contraseña va en un archivo de esta PC, junto a `secret_key`, y no en la
base: la base se sincroniza entre PCs y la contraseña es de este servidor.

Si todavía no hay contraseña, solo se puede crear desde la misma PC
(localhost), para que nadie de la red la elija primero. Para cambiarla:
`python -m app.acceso`.
"""

import getpass
import hmac
import time
from datetime import timedelta

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from . import db
from .routes import ruta_interna

bp = Blueprint("acceso", __name__)

LARGO_MINIMO = 8
INTENTOS_MAXIMOS = 5
BLOQUEO_SEGUNDOS = 5 * 60
DURACION_SESION = timedelta(days=30)

# IP -> (intentos fallidos seguidos, bloqueada hasta [epoch]).
_fallidos: dict[str, tuple[int, float]] = {}


def _ruta():
    return db.DATA_DIR / "clave_acceso"


def _hash_guardado() -> str | None:
    try:
        return _ruta().read_text().strip() or None
    except OSError:
        return None


def guardar_clave(clave: str) -> None:
    if len(clave) < LARGO_MINIMO:
        raise ValueError(f"La contraseña tiene que tener al menos {LARGO_MINIMO} caracteres.")
    ruta = _ruta()
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(generate_password_hash(clave))
    try:
        ruta.chmod(0o600)
    except OSError:
        pass


def _huella(hash_: str) -> str:
    """Identifica la contraseña vigente en la sesión: al cambiarla, las
    sesiones abiertas con la anterior dejan de valer."""
    return hash_[-16:]


def _es_local() -> bool:
    return request.remote_addr in ("127.0.0.1", "::1")


def _bloqueada(ip: str) -> int:
    """Segundos que le faltan a esa IP para poder volver a intentar."""
    _, hasta = _fallidos.get(ip, (0, 0.0))
    return max(0, int(hasta - time.time()))


def _fallo(ip: str) -> None:
    intentos, _ = _fallidos.get(ip, (0, 0.0))
    intentos += 1
    if intentos >= INTENTOS_MAXIMOS:
        _fallidos[ip] = (0, time.time() + BLOQUEO_SEGUNDOS)
    else:
        _fallidos[ip] = (intentos, 0.0)


def init_app(app):
    app.register_blueprint(bp)
    app.config.setdefault("ACCESO_CON_CLAVE", False)
    app.config.setdefault("PERMANENT_SESSION_LIFETIME", DURACION_SESION)
    # Lax: otro sitio no puede mandar formularios (POST) con la sesión.
    app.config.setdefault("SESSION_COOKIE_SAMESITE", "Lax")

    def _exigir_clave():
        if not app.config["ACCESO_CON_CLAVE"]:
            return None
        if request.blueprint == "acceso" or request.endpoint == "static":
            return None
        hash_ = _hash_guardado()
        if hash_ and hmac.compare_digest(session.get("acceso", ""), _huella(hash_)):
            return None
        # Navegando se va al login; los fetch de app.js reciben un 401.
        if request.method == "GET" and request.headers.get("Sec-Fetch-Mode", "navigate") == "navigate":
            return redirect(url_for("acceso.entrar", siguiente=request.full_path.rstrip("?")))
        return "Iniciá sesión.", 401

    # Primero que cualquier otro before_request (ej. el que refresca la
    # cotización): sin sesión no se ejecuta nada de la app.
    app.before_request_funcs.setdefault(None, []).insert(0, _exigir_clave)

    @app.context_processor
    def _acceso():
        return {"acceso_activo": app.config["ACCESO_CON_CLAVE"]}


@bp.route("/acceso", methods=["GET", "POST"])
def entrar():
    siguiente = ruta_interna(request.values.get("siguiente"), url_for("hoy.inicio"))
    hash_ = _hash_guardado()
    if not current_app.config["ACCESO_CON_CLAVE"]:
        return redirect(siguiente)

    if hash_ is None:
        # Primera vez: crear la contraseña, solo desde esta misma PC.
        if request.method == "POST" and _es_local():
            clave = request.form.get("clave", "")
            if clave != request.form.get("repetir", ""):
                flash("Las contraseñas no coinciden.", "error")
            else:
                try:
                    guardar_clave(clave)
                except ValueError as exc:
                    flash(str(exc), "error")
                else:
                    session.clear()
                    session.permanent = True
                    session["acceso"] = _huella(_hash_guardado())
                    return redirect(siguiente)
        return render_template("acceso.html", modo="crear", local=_es_local(),
                               siguiente=siguiente, largo_minimo=LARGO_MINIMO)

    if request.method == "POST":
        ip = request.remote_addr or ""
        espera = _bloqueada(ip)
        if espera:
            flash(f"Demasiados intentos. Probá de nuevo en {espera // 60 + 1} min.", "error")
        elif check_password_hash(hash_, request.form.get("clave", "")):
            _fallidos.pop(ip, None)
            session.clear()
            session.permanent = True
            session["acceso"] = _huella(hash_)
            return redirect(siguiente)
        else:
            _fallo(ip)
            current_app.logger.warning("Contraseña incorrecta desde %s", ip)
            flash("Contraseña incorrecta.", "error")
    return render_template("acceso.html", modo="entrar", siguiente=siguiente)


@bp.route("/acceso/salir", methods=["POST"])
def salir():
    session.clear()
    return redirect(url_for("acceso.entrar"))


if __name__ == "__main__":
    clave = getpass.getpass("Nueva contraseña de acceso: ")
    if clave != getpass.getpass("Repetila: "):
        raise SystemExit("No coinciden; no se cambió nada.")
    try:
        guardar_clave(clave)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    print(f"Contraseña guardada en {_ruta()}. Las sesiones abiertas se cierran.")
