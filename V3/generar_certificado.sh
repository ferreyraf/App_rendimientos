#!/usr/bin/env bash
# Certificado HTTPS para usar Bitácora en la red de casa (`run.py`).
#
# Crea (una sola vez) una autoridad certificante propia y con ella firma un
# certificado para esta PC: localhost, su nombre y sus IPs de la red. Todo
# queda en instance/certs/ (fuera de git). Volvé a correrlo si cambia la IP
# de la PC: reutiliza la autoridad, así no hay que reinstalarla en los
# dispositivos.
#
# Para que el navegador no avise, cada dispositivo tiene que confiar en
# instance/certs/bitacora-ca.crt (ver README).
set -euo pipefail
cd "$(dirname "$0")"

dir=instance/certs
mkdir -p "$dir"
chmod 700 "$dir"

if [ ! -f "$dir/bitacora-ca.key" ]; then
  openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -sha256 \
    -keyout "$dir/bitacora-ca.key" -out "$dir/bitacora-ca.crt" \
    -subj "/CN=Bitacora CA local" \
    -addext "basicConstraints=critical,CA:TRUE" \
    -addext "keyUsage=critical,keyCertSign,cRLSign"
  chmod 600 "$dir/bitacora-ca.key"
fi

san="DNS:localhost,DNS:$(hostname),DNS:$(hostname).local,IP:127.0.0.1"
for ip in $(hostname -I 2>/dev/null || ip -4 -o addr show scope global | awk '{split($4, a, "/"); print a[1]}'); do
  case "$ip" in
    *:*|172.1[6-9].*|172.2[0-9].*|172.3[01].*) ;;  # IPv6 y redes de Docker
    *) san="$san,IP:$ip" ;;
  esac
done

openssl req -newkey rsa:2048 -nodes -sha256 \
  -keyout "$dir/key.pem" -out "$dir/cert.csr" -subj "/CN=Bitacora"
# 825 días: el máximo que aceptan iPhone/macOS para certificados de servidor.
openssl x509 -req -in "$dir/cert.csr" -days 825 -sha256 \
  -CA "$dir/bitacora-ca.crt" -CAkey "$dir/bitacora-ca.key" -CAcreateserial \
  -out "$dir/cert.pem" \
  -extfile <(printf "subjectAltName=%s\nextendedKeyUsage=serverAuth\nbasicConstraints=CA:FALSE\n" "$san")
rm -f "$dir/cert.csr"
chmod 600 "$dir/key.pem"

echo "Certificado listo para: $san"
