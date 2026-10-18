import os

from app import create_app, db

app = create_app()
# Servida a la red: se entra con contraseña (ver app/acceso.py).
app.config["ACCESO_CON_CLAVE"] = os.environ.get("ACCESO", "1") != "0"

if __name__ == "__main__":
    # HTTPS si ya se generó el certificado (./generar_certificado.sh);
    # `HTTPS=0` lo apaga.
    certs = db.DATA_DIR / "certs"
    ssl_context = None
    if os.environ.get("HTTPS", "1") != "0" and (certs / "cert.pem").exists():
        ssl_context = (str(certs / "cert.pem"), str(certs / "key.pem"))
        app.config["SESSION_COOKIE_SECURE"] = True
    # `DEBUG=1` (recarga sola al editar el código) solo escucha en esta PC:
    # el depurador de Werkzeug permite ejecutar código y no debe quedar
    # expuesto a la red.
    debug = os.environ.get("DEBUG") == "1"
    host = os.environ.get("HOST", "127.0.0.1" if debug else "0.0.0.0")
    # 5003 para no chocar con la V1/V2 (5000); `PORT` lo cambia (ej. en un servidor).
    app.run(debug=debug, host=host, port=int(os.environ.get("PORT", 5003)), ssl_context=ssl_context)
