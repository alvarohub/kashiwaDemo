#!/usr/bin/env bash
# Generate a self-signed TLS cert for the SolarTurtle web chat so the in-page
# microphone (Web Speech API) works — browsers require a secure context.
# Run ON THE PI:   bash make_cert.sh
# Then start the server with:  python3 web_server.py --https
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CERT="$DIR/certs/selfsigned.pem"
KEY="$DIR/certs/selfsigned.key"

mkdir -p "$DIR/certs"

# Include the AP address and the Ethernet dev address as SANs so browsers
# match the cert to both. CN is just informational.
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout "$KEY" -out "$CERT" -days 825 \
  -subj "/CN=solarturtle.local" \
  -addext "subjectAltName=IP:10.42.0.1,IP:192.168.2.13,DNS:solarturtle.local,DNS:emotionpi.local"

echo "Wrote:"
echo "  $CERT"
echo "  $KEY"
echo
echo "Start HTTPS with:  python3 web_server.py --https"
echo "Then open:         https://10.42.0.1:8443"
echo "Each visitor accepts the self-signed warning once per device."
