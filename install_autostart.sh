#!/usr/bin/env bash
# Install/uninstall unattended-boot behavior for the SolarTurtle Pi:
#   - web server starts at boot (systemd, waits for Ollama)
#   - Wi-Fi AP profile already has autoconnect (created by ap_up.sh)
#   - Pi boots to console (no desktop) to save RAM
#
# Run ON THE PI:   sudo bash install_autostart.sh
# Undo:            sudo bash install_autostart.sh --uninstall
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo" >&2
  exit 1
fi

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# The user the node runs as: whoever invoked sudo (so this works for any
# username, not just the one this was developed on). The service files and
# cron entries are adapted to this user + home below.
TARGET_USER="${SUDO_USER:-admin}"
TARGET_HOME="$(getent passwd "$TARGET_USER" 2>/dev/null | cut -d: -f6 || true)"
[ -n "$TARGET_HOME" ] || TARGET_HOME="/home/$TARGET_USER"

if [[ "${1:-}" == "--uninstall" ]]; then
  systemctl disable --now solarturtle-web.service 2>/dev/null || true
  rm -f /etc/systemd/system/solarturtle-web.service
  systemctl disable --now solarturtle-voice.service 2>/dev/null || true
  rm -f /etc/systemd/system/solarturtle-voice.service
  systemctl daemon-reload
  echo "solarturtle-web service removed. (AP profile 'solarturtle-ap' left in place;"
  echo " remove with: nmcli con delete solarturtle-ap)"
  exit 0
fi

# Stop anything already running FIRST, so re-running this script is safe and
# always ends with the freshly-installed units running the latest code.
# (Without this, `enable --now` leaves an old process running if it's already
# up, and a half-updated node is exactly the class of bug we keep chasing.)
systemctl stop solarturtle-web solarturtle-voice solarturtle-sensors 2>/dev/null || true

# 1. web server service (user + paths adapted to this machine)
sed -e "s|/home/admin|$TARGET_HOME|g" -e "s|^User=admin$|User=$TARGET_USER|" \
  "$SRC_DIR/solarturtle-web.service" > /etc/systemd/system/solarturtle-web.service
systemctl daemon-reload
systemctl enable --now solarturtle-web.service

# 1b. sensor loggers service (registry-driven, sensors.json)
sed -e "s|/home/admin|$TARGET_HOME|g" -e "s|^User=admin$|User=$TARGET_USER|" \
  "$SRC_DIR/solarturtle-sensors.service" > /etc/systemd/system/solarturtle-sensors.service
systemctl daemon-reload
systemctl enable --now solarturtle-sensors.service

# 1b2. voice loop — idle until the web page turns the microphone on.
# Needs the voice venv (setup_voice.sh); skipped cleanly if it isn't there.
if [ -x "$TARGET_HOME/voice-venv/bin/python" ]; then
  sed -e "s|/home/admin|$TARGET_HOME|g" -e "s|^User=admin$|User=$TARGET_USER|" \
    "$SRC_DIR/solarturtle-voice.service" > /etc/systemd/system/solarturtle-voice.service
  systemctl daemon-reload
  systemctl enable --now solarturtle-voice.service
else
  echo "NOTE: no ~/voice-venv — skipping the voice service (run setup_voice.sh first)."
fi

# 1c. daily journal entry (23:55) — the mechanical memory writer, as the
# node's user. Installed into that user's crontab (not root's): the journal
# lives in the repo folder and must stay owned by its user. Also clean out
# any copy a previous sudo-run left in root's crontab.
CRON_TMP="$(mktemp)"
{ crontab -u "$TARGET_USER" -l 2>/dev/null || true; } | grep -v journal_writer > "$CRON_TMP" || true
echo "55 23 * * * /usr/bin/python3 $TARGET_HOME/solarturtle/loggers/journal_writer.py" >> "$CRON_TMP"
crontab -u "$TARGET_USER" "$CRON_TMP"
rm -f "$CRON_TMP"
crontab -l 2>/dev/null | grep -v journal_writer | crontab - 2>/dev/null || true

# 1d. keep the model resident: Ollama evicts it 5 min after the last question
# by default, which costs ~8 s of reload on the next visitor. A kami should
# answer instantly. (~1 GB RAM held permanently; undo by deleting this file.)
mkdir -p /etc/systemd/system/ollama.service.d
cat > /etc/systemd/system/ollama.service.d/keepalive.conf <<'EOF'
[Service]
Environment="OLLAMA_KEEP_ALIVE=-1"
EOF
systemctl daemon-reload
systemctl restart ollama

# 1e. let ./push_to_pi.sh and ./start_kami.sh/./stop_kami.sh manage the
# services without a password prompt. Scoped to exactly these commands —
# not a general sudo grant.
cat > /etc/sudoers.d/solarturtle-restart <<EOF
$TARGET_USER ALL=(root) NOPASSWD: /usr/bin/systemctl start solarturtle-web, /usr/bin/systemctl stop solarturtle-web, /usr/bin/systemctl restart solarturtle-web, /usr/bin/systemctl start solarturtle-voice, /usr/bin/systemctl stop solarturtle-voice, /usr/bin/systemctl restart solarturtle-voice, /usr/bin/systemctl start solarturtle-sensors, /usr/bin/systemctl stop solarturtle-sensors, /usr/bin/systemctl restart solarturtle-sensors, /usr/bin/systemctl start ollama, /usr/bin/systemctl stop ollama, /usr/bin/systemctl restart ollama
EOF
chmod 0440 /etc/sudoers.d/solarturtle-restart
visudo -c -q || { echo "BAD sudoers file — removing"; rm -f /etc/sudoers.d/solarturtle-restart; }

# 2. boot to console (no desktop) — saves ~250 MB RAM
systemctl set-default multi-user.target

# 3. make sure the AP profile autoconnects if it exists
if nmcli -t -f NAME con show | grep -qx "solarturtle-ap"; then
  nmcli con mod solarturtle-ap connection.autoconnect yes
  echo "AP profile 'solarturtle-ap' set to autoconnect."
else
  echo "NOTE: no solarturtle-ap profile yet — run 'sudo bash ap_up.sh' once first."
fi

echo
echo "=== autostart installed ==="
systemctl is-enabled solarturtle-web.service
systemctl is-enabled solarturtle-sensors.service
echo "Default boot target: $(systemctl get-default)"
echo "Reboot to verify: sudo reboot"
echo "After reboot the chat should be at http://10.42.0.1:8080 (via AP)"
echo "or http://$(hostname -I | awk '{print $1}'):8080 (via your network)."
