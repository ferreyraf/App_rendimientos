"""Imprime el texto de la Release de GitHub de una versión, sacado de
app/novedades.py (lo usa el workflow al publicar).

    python V3/packaging/notas_release.py bitacora-v3.3.0
"""

import runpy
import sys
from pathlib import Path

# Se carga el archivo suelto: importar el paquete `app` necesitaría Flask.
novedades = runpy.run_path(str(Path(__file__).resolve().parent.parent / "app" / "novedades.py"))
version = sys.argv[1].removeprefix("bitacora-v")
texto = novedades["markdown"](version)
if not texto:
    sys.exit(f"Falta la versión {version} en app/novedades.py")
print(texto)
