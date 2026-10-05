#!/usr/bin/env bash
# set_autostart.sh — turn the node's autostart ON or OFF.
#
#   sudo bash set_autostart.sh on    installs the node's systemd services
#                                    (web + sensors + voice) and the daily
#                                    journal cron entry, and starts the node
#                                    NOW — from then on it starts by itself
#                                    at every boot.
#   sudo bash set_autostart.sh off   stops and removes everything (services,
#                                    cron entry, settings) and cleans up any
#                                    leftover node processes. Ollama is left
#                                    running: stop it with
#                                    "sudo systemctl stop ollama".
#   bash set_autostart.sh            show usage + the current state.
#
# Run ON THE PI, from the repository folder.
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# The user the node runs as: whoever invoked sudo (so this works for any
# username, not just the one this was developed on). The service files and
# cron entries are adapted to this user + home below.
TARGET_USER="${SUDO_USER:-admin}"
TARGET_HOME="$(getent passwd "$TARGET_USER" 2>/dev/null | cut -d: -f6 || true)"
[ -n "$TARGET_HOME" ] || TARGET_HOME="/home/$TARGET_USER"

SERVICES=(solarturtle-web solarturtle-sensors solarturtle-voice)

usage() {
  echo "usage: sudo bash set_autostart.sh on|off"
  echo
  echo "current state:"
  for s in "${SERVICES[@]}"; do
    state="$(systemctl is-enabled "$s.service" 2>/dev/null || echo "not installed")"
    printf '  %-28s %s\n' "$s.service" "$state"
  done
}

# Kill leftover node processes from earlier runs. systemd only stops its own
# cgroup: a hand-started ./kami or a half-dead service can leave web /
# sensors / loggers / voice / arecord processes behind — the "annoying
# leftovers". Called by both ON (clean slate) and OFF (real teardown).
kill_strays() {
  local killed=0
  if pkill -f "(python|python3) .*web_server\.py" 2>/dev/null; then killed=1; fi
  if pkill -f "(python|python3) .*sensors_boot\.py" 2>/dev/null; then killed=1; fi
  if pkill -f "(python|python3) .*loggers/sensor_" 2>/dev/null; then killed=1; fi
  if pkill -f "(python|python3) .*voice/talk\.py" 2>/dev/null; then killed=1; fi
  if pkill -f "arecord.*kami_" 2>/dev/null; then killed=1; fi
  if [ "$killed" = "1" ]; then
    echo "(stopped leftover node processes from earlier runs)"
  fi
  return 0
}

MODE="${1:-}"
if [ "$MODE" = "" ]; then
  usage
  exit 0
fi
if [ "$MODE" != "on" ] && [ "$MODE" != "off" ]; then
  usage
  exit 1
fi

if [ "$EUID" -ne 0 ]; then
  echo "Run with sudo:  sudo bash set_autostart.sh $MODE" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# OFF — stop the node, remove the services and their settings, clean up
# ---------------------------------------------------------------------------
if [ "$MODE" = "off" ]; then
  echo "Turning autostart OFF..."

  for s in "${SERVICES[@]}"; do
    systemctl disable --now "$s.service" 2>/dev/null || true
    rm -f "/etc/systemd/system/$s.service"
  done
  systemctl reset-failed "${SERVICES[@]}" 2>/dev/null || true
  systemctl daemon-reload

  # the daily journal cron entry (the node's user's crontab)
  CRON_TMP="$(mktemp)"
  { crontab -u "$TARGET_USER" -l 2>/dev/null || true; } | grep -v journal_writer > "$CRON_TMP" || true
  crontab -u "$TARGET_USER" "$CRON_TMP" 2>/dev/null || true
  rm -f "$CRON_TMP"

  # the passwordless-sudo rule and the model keep-alive setting
  rm -f /etc/sudoers.d/solarturtle-restart
  rm -f /etc/systemd/system/ollama.service.d/keepalive.conf
  systemctl daemon-reload
  systemctl restart ollama 2>/dev/null || true

  kill_strays

  echo
  echo "=== autostart is OFF ==="
  echo "Stopped:   web chat + sensors + voice (services removed; leftovers cleaned)."
  echo "Kept:      Ollama is running (sudo systemctl stop ollama to stop it);"
  echo "           the Wi-Fi AP profile and the boot target are unchanged."
  echo "Run again: ./kami  (manual run)   —   or:  sudo bash set_autostart.sh on"
  exit 0
fi

# ---------------------------------------------------------------------------
# ON — install the services (+ settings) and start the node now
# ---------------------------------------------------------------------------
# Stop everything that may already be running FIRST, so re-running this is
# always safe and ends with the freshly-installed units on the latest code.
for s in "${SERVICES[@]}"; do
  systemctl stop "$s.service" 2>/dev/null || true
done
kill_strays

# 1. services (user + paths adapted to this machine)
sed -e "s|/home/admin|$TARGET_HOME|g" -e "s|^User=admin$|User=$TARGET_USER|" \
  "$SRC_DIR/solarturtle-web.service" > /etc/systemd/system/solarturtle-web.service
sed -e "s|/home/admin|$TARGET_HOME|g" -e "s|^User=admin$|User=$TARGET_USER|" \
  "$SRC_DIR/solarturtle-sensors.service" > /etc/systemd/system/solarturtle-sensors.service
systemctl daemon-reload
systemctl enable --now solarturtle-web.service
systemctl enable --now solarturtle-sensors.service

# voice loop — idles until the web page turns the microphone on; skipped
# cleanly if the voice venv isn't there yet (run setup_voice.sh).
if [ -x "$TARGET_HOME/voice-venv/bin/python" ]; then
  sed -e "s|/home/admin|$TARGET_HOME|g" -e "s|^User=admin$|User=$TARGET_USER|" \
    "$SRC_DIR/solarturtle-voice.service" > /etc/systemd/system/solarturtle-voice.service
  systemctl daemon-reload
  systemctl enable --now solarturtle-voice.service
else
  echo "NOTE: no ~/voice-venv — skipping the voice service (run setup_voice.sh first)."
fi

# 2. daily journal entry (23:55) — the mechanical memory writer, as the
# node's user. Installed into that user's crontab (not root's): the journal
# lives in the repo folder and must stay owned by its user. Also clean out
# any copy a previous sudo-run left in root's crontab.
CRON_TMP="$(mktemp)"
{ crontab -u "$TARGET_USER" -l 2>/dev/null || true; } | grep -v journal_writer > "$CRON_TMP" || true
echo "55 23 * * * /usr/bin/python3 $TARGET_HOME/solarturtle/loggers/journal_writer.py" >> "$CRON_TMP"
crontab -u "$TARGET_USER" "$CRON_TMP"
rm -f "$CRON_TMP"
crontab -l 2>/dev/null | grep -v journal_writer | crontab - 2>/dev/null || true

# 3. keep the model resident: Ollama evicts it 5 min after the last question
# by default, which costs ~8 s of reload on the next visitor. A kami should
# answer instantly. (~1 GB RAM held permanently; removed by "off".)
mkdir -p /etc/systemd/system/ollama.service.d
cat > /etc/systemd/system/ollama.service.d/keepalive.conf <<'EOF'
[Service]
Environment="OLLAMA_KEEP_ALIVE=-1"
EOF
systemctl daemon-reload
systemctl restart ollama

# 4. let push_to_pi.sh and start_kami.sh/stop_kami.sh manage the services
# without a password prompt. Scoped to exactly these commands — not a
# general sudo grant.
cat > /etc/sudoers.d/solarturtle-restart <<EOF
$TARGET_USER ALL=(root) NOPASSWD: /usr/bin/systemctl start solarturtle-web, /usr/bin/systemctl stop solarturtle-web, /usr/bin/systemctl restart solarturtle-web, /usr/bin/systemctl start solarturtle-voice, /usr/bin/systemctl stop solarturtle-voice, /usr/bin/systemctl restart solarturtle-voice, /usr/bin/systemctl start solarturtle-sensors, /usr/bin/systemctl stop solarturtle-sensors, /usr/bin/systemctl restart solarturtle-sensors, /usr/bin/systemctl start ollama, /usr/bin/systemctl stop ollama, /usr/bin/systemctl restart ollama
EOF
chmod 0440 /etc/sudoers.d/solarturtle-restart
visudo -c -q || { echo "BAD sudoers file — removing"; rm -f /etc/sudoers.d/solarturtle-restart; }

# 5. boot to console (no desktop) — saves ~250 MB RAM
systemctl set-default multi-user.target

# 6. make the AP profile autoconnect if it exists
if nmcli -t -f NAME con show | grep -qx "solarturtle-ap"; then
  nmcli con mod solarturtle-ap connection.autoconnect yes
  echo "AP profile 'solarturtle-ap' set to autoconnect."
else
  echo "NOTE: no solarturtle-ap profile yet — run 'sudo bash ap_up.sh' once first."
fi

echo
echo "=== autostart is ON — the node is RUNNING ==="
echo "Now:        web chat + sensors + voice (if set up) are running."
echo "At boot:    they will start automatically, every time."
echo "Chat page:  http://$(hostname -I | awk '{print $1}'):8080   (via AP: http://10.42.0.1:8080)"
echo "Turn off:   sudo bash set_autostart.sh off"
