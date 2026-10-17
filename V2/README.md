# Rulo Financiero — V2

App de escritorio para seguir el **rulo financiero** (rotación de la plata entre billeteras virtuales para cobrar el rendimiento diario de cada una), junto con gastos, ingresos, ajustes por inflación y gastos en dólares.

La versión actual está en `app/version.py` y se ve en la barra superior y en **Acerca de**.

## Instalar

### Linux

```sh
./RuloFinanciero-2.0.0-x86_64.AppImage --instalar
```

Queda en el menú de aplicaciones y como comando `rulo-financiero`. No necesita `sudo`.
Para actualizar, instalá el AppImage nuevo de la misma forma.
Para desinstalar: `rulo-financiero --desinstalar` (agregá `--borrar-datos` para borrar también tus datos).

### Windows

Ejecutá `RuloFinanciero-2.0.0-Setup.exe`. Instala para tu usuario (sin permisos de administrador), con acceso directo y desinstalador en "Agregar o quitar programas". Usa WebView2 (Edge), que ya viene con Windows 10/11.

### Dónde quedan los datos

| Sistema | Carpeta |
|---|---|
| Linux | `~/.local/share/RuloFinanciero/` |
| Windows | `%LOCALAPPDATA%\RuloFinanciero\` |
| Desarrollo | `V2/instance/` |

Ahí están la base (`rendimientos.db`), los backups automáticos y los logs. Actualizar o desinstalar la app no la borra. Se puede forzar otra carpeta con la variable `RULO_DATA_DIR`.

## Desarrollo

```sh
./start.sh       # versión web en http://localhost:5000
./desktop.sh     # ventana de escritorio
```

## Compilar los instaladores

```sh
.venv/bin/pip install -r requirements-build.txt
.venv/bin/python packaging/build.py
```

Genera en `dist/release/` el instalador del sistema donde se corre: AppImage en Linux, `Setup.exe` en Windows (requiere [Inno Setup 6](https://jrsoftware.org/isdl.php)).

### Publicar una versión

1. Subí el número en `app/version.py` (ej. `2.1.0`).
2. Commit y tag: `git tag v2.1.0 && git push --tags`.
3. GitHub Actions (`.github/workflows/build-v2.yml`, en la raíz del repo) compila Windows y Linux y publica una Release con los dos instaladores.

También se puede correr a mano desde la pestaña **Actions** sin crear una Release.

## Estructura

- `app/` — Flask + SQLite: dominio (`domain.py`), datos (`db.py`), pantallas (`routes.py`, `templates/`), cotización del dólar (`cotizacion.py`).
- `desktop.py` — ventana nativa con pywebview (Qt WebEngine en Linux, WebView2 en Windows), una sola instancia a la vez.
- `packaging/` — build (`build.py`), íconos, AppImage (`linux/`) e instalador de Windows (`windows/RuloFinanciero.iss`).
