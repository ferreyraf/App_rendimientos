"""Certificados para las consultas por HTTPS (GitHub, dólar, IPC).

El Python empaquetado busca los certificados donde los tenía el sistema en
el que se compiló: el AppImage de GitHub Actions (Ubuntu) los busca en
/usr/lib/ssl y en otras distros (Arch, Fedora…) no están ahí, así que toda
consulta falla con CERTIFICATE_VERIFY_FAILED. Si pasa eso, se le indica a
OpenSSL dónde están en este sistema."""

import logging
import os
import ssl
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

CANDIDATOS = (
    "/etc/ssl/certs/ca-certificates.crt",  # Debian, Ubuntu, Arch
    "/etc/pki/tls/certs/ca-bundle.crt",  # Fedora, RHEL
    "/etc/ssl/ca-bundle.pem",  # openSUSE
    "/etc/ssl/cert.pem",  # Alpine, Arch
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",
)


def configurar() -> None:
    if not sys.platform.startswith("linux") or os.environ.get("SSL_CERT_FILE"):
        return
    rutas = ssl.get_default_verify_paths()
    if rutas.cafile and Path(rutas.cafile).is_file():
        return
    encontrado = next((c for c in CANDIDATOS if Path(c).is_file()), None)
    if encontrado:
        os.environ["SSL_CERT_FILE"] = encontrado
        logger.info("Certificados HTTPS: %s", encontrado)
    else:
        logger.warning("No se encontraron los certificados HTTPS del sistema")
