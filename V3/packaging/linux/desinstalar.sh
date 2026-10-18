#!/bin/sh
# Desinstala @APP_NAME@. Tus datos (base, backups, logs) se conservan, salvo
# que se pase --borrar-datos.
set -e

APP_ID="@APP_ID@"
DATOS="${XDG_DATA_HOME:-$HOME/.local/share}/$APP_ID"
APLICACIONES="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONOS="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor"

rm -f "$APLICACIONES/bitacora.desktop"
find "$ICONOS" -name "bitacora.png" -path "*/apps/*" -delete 2>/dev/null || true
rm -f "$HOME/.local/bin/bitacora"
rm -rf "$HOME/.local/opt/$APP_ID"

update-desktop-database "$APLICACIONES" >/dev/null 2>&1 || true
gtk-update-icon-cache -f -t "$ICONOS" >/dev/null 2>&1 || true

if [ "$1" = "--borrar-datos" ]; then
  rm -rf "$DATOS"
  echo "✔ @APP_NAME@ desinstalado y datos borrados."
else
  echo "✔ @APP_NAME@ desinstalado. Tus datos quedaron en: $DATOS"
fi
