#!/usr/bin/env bash
# Captive portal for the SolarTurtle access point (Phase 2B.3, Option B).
# Makes a phone that joins the SolarTurtle Wi-Fi pop the chat page open
# automatically (like hotel Wi-Fi), instead of typing an URL.
#
# How: nmcli's "shared" mode already runs dnsmasq for DHCP/DNS. We drop a
# wildcard-DNS snippet into its config dir so EVERY hostname resolves to the
# Pi, then the web server answers the OS's captive-portal probes
# (/generate_204, /hotspot-detect.html, ...) with a redirect to the chat.
#
# Run ON THE PI:   sudo bash portal_up.sh        (enable)
#                  sudo bash portal_down.sh      (disable)
set -euo pipefail

PI_AP_IP="10.42.0.1"
SNIPPET_DIR="/etc/NetworkManager/dnsmasq-shared.d"
SNIPPET="$SNIPPET_DIR/solarturtle-portal.conf"

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo:  sudo bash portal_up.sh" >&2
  exit 1
fi

mkdir -p "$SNIPPET_DIR"
cat > "$SNIPPET" <<EOF
# SolarTurtle captive portal: resolve everything to the Pi so OS captive-portal
# detection fires and any typed URL lands on the chat.
address=/#/$PI_AP_IP
EOF

echo "Wrote $SNIPPET"

# Restart the AP connection so dnsmasq reloads with the snippet.
if nmcli -t -f NAME con show | grep -qx "solarturtle-ap"; then
  nmcli con down solarturtle-ap 2>/dev/null || true
  nmcli con up solarturtle-ap
  echo "AP restarted with portal DNS."
else
  echo "NOTE: no solarturtle-ap profile yet — run 'sudo bash ap_up.sh' first."
fi

echo
echo "Portal ON. Phones joining SolarTurtle should now pop the chat page open."
echo "If a phone does not pop, open any http:// site (e.g. http://neverssl.com)."
