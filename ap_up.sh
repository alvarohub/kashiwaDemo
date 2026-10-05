#!/usr/bin/env bash
# Set up the Pi's wlan0 as a Wi-Fi access point (Phase 2B, Option A).
# Run ON THE PI:   sudo bash ap_up.sh [SSID] [PASSWORD]
# Defaults: ssid SolarTurtle, password emotionpi
#
# Result: Pi broadcasts SSID; clients get DHCP and reach the Pi at 10.42.0.1.
# The web chat is then at http://10.42.0.1:8080
# Ethernet (eth0 -> Mac dev link) is untouched and keeps working.
set -euo pipefail

SSID="${1:-SolarTurtle}"
PASS="${2:-emotionpi}"

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo:  sudo bash ap_up.sh [SSID] [PASSWORD]" >&2
  exit 1
fi

# Set regulatory domain if it is still the global/unset one (needed for AP).
if ! iw reg get | grep -q "country"; then
  echo "WARNING: no Wi-Fi country set. Run: sudo raspi-config nonint do_wifi_country FR"
fi

nmcli radio wifi on

# If an AP profile already exists, reuse it; otherwise create it.
if nmcli -t -f NAME con show | grep -qx "solarturtle-ap"; then
  nmcli con mod solarturtle-ap wifi.ssid "$SSID" wifi-sec.psk "$PASS"
  nmcli con up solarturtle-ap
else
  # Create a shared-mode AP on wlan0. 'shared' gives DHCP+NAT at 10.42.0.1.
  nmcli con add type wifi ifname wlan0 con-name solarturtle-ap \
    autoconnect yes ssid "$SSID"
  nmcli con mod solarturtle-ap 802-11-wireless.mode ap \
    802-11-wireless.band bg \
    ipv4.method shared \
    ipv6.method disabled \
    wifi-sec.key-mgmt wpa-psk \
    wifi-sec.psk "$PASS"
  nmcli con up solarturtle-ap
fi

echo
echo "=== AP up ==="
nmcli -f DEVICE,TYPE,STATE,CONNECTION dev status
echo "SSID: $SSID   password: $PASS"
echo "Pi AP address: $(ip -4 addr show wlan0 | awk '/inet /{print $2}')"
echo "Chat page: http://10.42.0.1:8080"
