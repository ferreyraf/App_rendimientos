#!/bin/sh
# Instala @APP_NAME@ @VERSION@ para el usuario actual (no necesita sudo).
# También sirve para actualizar: reemplaza la versión instalada y conserva los datos.
set -e

APP_ID="@APP_ID@"
HERE="$(dirname "$(readlink -f "$0")")"
ORIGEN="${APPIMAGE:-}"
if [ -z "$ORIGEN" ]; then
  echo "Ejecutá el instalador desde el archivo .AppImage: ./${APP_ID}-@VERSION@-x86_64.AppImage --instalar" >&2
  exit 1
fi

DESTINO="$HOME/.local/opt/$APP_ID"
INSTALADO="$DESTINO/$APP_ID.AppImage"
APLICACIONES="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONOS="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor"

mkdir -p "$DESTINO" "$HOME/.local/bin" "$APLICACIONES" "$ICONOS"

if [ "$(readlink -f "$ORIGEN")" != "$(readlink -f "$INSTALADO" 2>/dev/null || true)" ]; then
  cp -f "$ORIGEN" "$INSTALADO.nuevo"
  chmod 755 "$INSTALADO.nuevo"
  mv -f "$INSTALADO.nuevo" "$INSTALADO"
fi
ln -sf "$INSTALADO" "$HOME/.local/bin/rulo-financiero"

cp -rf "$HERE/usr/share/icons/hicolor/." "$ICONOS/"
sed "s|^Exec=.*|Exec=\"$INSTALADO\"|" "$HERE/rulo-financiero.desktop" > "$APLICACIONES/rulo-financiero.desktop"
chmod 644 "$APLICACIONES/rulo-financiero.desktop"

update-desktop-database "$APLICACIONES" >/dev/null 2>&1 || true
gtk-update-icon-cache -f -t "$ICONOS" >/dev/null 2>&1 || true

echo "✔ @APP_NAME@ @VERSION@ instalado."
echo "  Abrilo desde el menú de aplicaciones o con el comando: rulo-financiero"
echo "  Programa: $INSTALADO"
