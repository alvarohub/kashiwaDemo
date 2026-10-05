#!/usr/bin/env bash
# Disable the captive portal (remove the wildcard DNS snippet).
# Run ON THE PI:   sudo bash portal_down.sh
set -euo pipefail

SNIPPET="/etc/NetworkManager/dnsmasq-shared.d/solarturtle-portal.conf"

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo:  sudo bash portal_down.sh" >&2
  exit 1
fi

if [[ -f "$SNIPPET" ]]; then
  rm -f "$SNIPPET"
  if nmcli -t -f NAME con show | grep -qx "solarturtle-ap"; then
    nmcli con down solarturtle-ap 2>/dev/null || true
    nmcli con up solarturtle-ap
  fi
  echo "Portal OFF (wildcard DNS removed, AP restarted)."
else
  echo "No portal snippet found; nothing to do."
fi
