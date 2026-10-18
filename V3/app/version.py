"""Identidad y versión de la app: única fuente de verdad para la interfaz,
la ventana de escritorio y los instaladores (packaging/build.py la lee)."""

APP_NAME = "Bitácora"
# Identificador sin espacios ni tildes: carpeta de datos, ejecutable y
# entrada de menú. Distinto del de la V2 ("RuloFinanciero") para que las dos
# instaladas no compartan datos.
APP_ID = "Bitacora"
__version__ = "3.0.0"
PUBLISHER = "Fer F"
DESCRIPTION = "Tu día en un solo lugar: rutina, hábitos, entrenamiento y finanzas (rulo entre billeteras, gastos e ingresos)"
# Releases publicadas (para avisar cuando hay una versión nueva).
REPO_GITHUB = "ferreyraf/App_rendimientos"
