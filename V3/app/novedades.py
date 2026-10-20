"""Qué trae cada versión, de la más nueva a la más vieja. Se muestra en
Acerca de y es el texto de la Release de GitHub (packaging/notas_release.py).
Al publicar una versión, sumarla acá arriba."""

NOVEDADES = (
    {
        "version": "3.3.0",
        "fecha": "2026-10-19",
        "titulo": "Actualizaciones desde la app",
        "cambios": (
            "Acerca de muestra las novedades de cada versión.",
            "Actualizar desde la propia app: elegís si instalar la versión nueva y si hacer un respaldo de tus datos antes; se descarga, se instala y la app se vuelve a abrir sola.",
            "Volver a una versión anterior, descargándola de GitHub o (en Linux) con la que quedó guardada al actualizar, sin internet.",
            "Recuperar datos: lista los respaldos automáticos y los de antes de cada actualización, y restaura el que elijas.",
            "Arreglo en Linux: el AppImage descargado de GitHub no podía consultar internet en algunas distros (aviso de versión nueva, dólar e IPC).",
        ),
    },
    {
        "version": "3.2.0",
        "fecha": "2026-10-19",
        "titulo": "Alimentación",
        "cambios": (
            "Sección nueva Alimentación con diario de comidas: anotás un alimento y cuánto (en gramos o por unidad) y calcula calorías, proteínas, carbohidratos y grasas.",
            "También se pueden cargar las macros a mano (ej. comida afuera) o guardar comidas frecuentes para anotarlas con un clic.",
            "Lista de alimentos propia, que arranca con básicos: pollo, huevo, repollo, papa, milanesa de soja, zanahoria, yogur casero, leche proteica, batata y boniato.",
            "Objetivos diarios calculados con tus datos (sexo, edad, altura, peso, actividad y objetivo) o puestos a mano.",
            "La pantalla Hoy suma las calorías del día y las barras de macros; sus indicadores van todos en una fila.",
        ),
    },
    {
        "version": "3.1.0",
        "fecha": "2026-10-18",
        "titulo": "Red de casa y aguinaldo",
        "cambios": (
            "La versión web se sirve por HTTPS en la red de casa, con certificado propio, para usarla desde el celular.",
            "Contraseña de acceso para la versión web (la de escritorio no la pide).",
            "Aguinaldo automático dividido en blanco y negro.",
        ),
    },
    {
        "version": "3.0.0",
        "fecha": "2026-10-18",
        "titulo": "Bitácora",
        "cambios": (
            "La app pasa a llamarse Bitácora: tu día en un solo lugar, con la pantalla Hoy de inicio.",
            "Rutina: hábitos con checklist y rachas, tareas, registro de sueño, ánimo y peso, agenda por horarios con avisos y alarmas.",
            "Entrenamiento: gimnasio (rutinas, series en vivo, récords y progreso) y cardio (distancia, tiempo y ritmo).",
            "Finanzas: todo lo de la V2 (el rulo entre billeteras, gastos, ingresos y dólares).",
            "Instalador de Windows con todo incluido y AppImage para Linux.",
        ),
    },
)


def de_version(version: str) -> dict | None:
    return next((n for n in NOVEDADES if n["version"] == version), None)


def markdown(version: str) -> str:
    """Texto de la Release de GitHub de `version`."""
    n = de_version(version)
    if n is None:
        return ""
    return f"## {n['titulo']}\n\n" + "\n".join(f"- {c}" for c in n["cambios"]) + "\n"
