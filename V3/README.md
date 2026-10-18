# Bitácora — V3

Tu día en un solo lugar:

- **Hoy**: la pantalla de inicio, con todo lo del día.
- **Rutina**: agenda por horarios, alarmas, hábitos con checklist, tareas, y registro de sueño, ánimo y peso.
- **Entrenamiento**: gimnasio (rutinas, series y récords) y cardio.
- **Finanzas**: el rulo entre billeteras virtuales, gastos, ingresos, ajustes por inflación y dólares (lo que era la V2).

La versión actual está en `app/version.py` y se ve en el menú y en **Acerca de**.

## Instalar

### Windows

Ejecutá `Bitacora-3.0.0-Setup.exe`. No hace falta instalar nada más, porque Python y todas las librerías van adentro.

- Instala para tu usuario, sin permisos de administrador.
- Crea el acceso directo y queda en "Agregar o quitar programas".
- Opcionalmente, se abre al iniciar Windows en segundo plano, para que suenen las alarmas.
- Usa WebView2 (el motor de Edge), que Windows 10/11 ya traen. Si falta, el instalador lo agrega.

Convive con la V2 (Rulo Financiero) instalada: cada una tiene sus propios datos.

### Linux

```sh
./Bitacora-3.0.0-x86_64.AppImage --instalar
```

Queda en el menú de aplicaciones y como comando `bitacora`. No necesita `sudo`.

- **Actualizar:** instalá el AppImage nuevo de la misma forma.
- **Desinstalar:** `bitacora --desinstalar`. Agregá `--borrar-datos` para borrar también tus datos.

### Dónde quedan los datos

| Sistema | Carpeta |
|---|---|
| Linux | `~/.local/share/Bitacora/` |
| Windows | `%LOCALAPPDATA%\Bitacora\` |
| Desarrollo | `V3/instance/` |

Ahí están la base (`rendimientos.db`), los backups automáticos y los logs. Actualizar o desinstalar la app no los borra. Se puede forzar otra carpeta con la variable `RULO_DATA_DIR`.

## Alarmas

Se crean en **Rutina → Alarmas**. Cada alarma tiene:

- una hora, y días de la semana o un día puntual;
- un sonido;
- los minutos para posponer.

Cuando suena aparece un aviso con **Detener** / **Posponer** en cualquier pantalla. Si nadie la atiende, se calla sola a los 15 minutos.

- **App de escritorio:** el sonido lo toca la propia app, así suena aunque la ventana esté escondida en la bandeja, y además trae la ventana al frente.
- **Navegador:** suena mientras haya una página de la app abierta. Si el navegador bloquea el audio, el aviso ofrece activarlo.

## Desarrollo

```sh
./start.sh       # versión web en http://localhost:5003
./desktop.sh     # ventana de escritorio
```

## Compilar los instaladores

```sh
.venv/bin/pip install -r requirements-build.txt
.venv/bin/python packaging/build.py
```

Genera en `dist/release/` el instalador del sistema donde se corre:

- **Linux:** el AppImage.
- **Windows:** el `Setup.exe`. Requiere [Inno Setup 6](https://jrsoftware.org/isdl.php).

### Publicar una versión

1. Subí el número en `app/version.py` (ej. `3.1.0`).
2. Hacé el commit y creá el tag con el prefijo `bitacora-v`: `git tag bitacora-v3.1.0 && git push origin bitacora-v3.1.0`. El prefijo hace que la V2 instalada no tome la release como suya.
3. GitHub Actions (`.github/workflows/build-v3.yml`, en la raíz del repo) compila Windows y Linux y publica una Release con los dos instaladores.

El workflow también se puede correr a mano desde la pestaña **Actions**; así no se crea una Release.

## Estructura

- `app/` — Flask + SQLite.
  - `hoy.py`: la pantalla Hoy, que junta lo que aporta cada sección.
  - `rutina/` y `entrenamiento/`: cada sección con su `logica.py`, `datos.py` y `rutas.py`.
  - Finanzas: `domain.py`, `db.py` y `routes.py`.
  - `sonidos.py`: los tonos de las alarmas, generados en Python, y el timbre nativo.
  - `recordatorios.py`: los avisos del sistema y el vigía de alarmas, que corren en la app de escritorio.
- `desktop.py` — ventana nativa con pywebview (Qt WebEngine en Linux, WebView2 en Windows), ícono en la bandeja y una sola instancia a la vez.
- `packaging/` — build (`build.py`), íconos (`icons/generar_icono.py`), AppImage (`linux/`) e instalador de Windows (`windows/Bitacora.iss`).
