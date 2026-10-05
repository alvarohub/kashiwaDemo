#!/usr/bin/env bash
# Push local SolarTurtle node code to the Pi over SSH. Run from the Mac, anywhere:
#   ./push_to_pi.sh            (uses default host below)
#   PI_HOST=admin@192.168.2.13 ./push_to_pi.sh
set -euo pipefail

PI_HOST="${PI_HOST:-admin@192.168.2.13}"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/"
DEST="${PI_HOST}:~/solarturtle/"

echo "Pushing $SRC -> $DEST"
# Files the PI owns are excluded both ways: they are written there (by the
# phone, the loggers, the voice loop) and would be clobbered by --delete.
# identity/identity.md is NOT excluded: the personality is authored here.
rsync -av --delete \
  --exclude '.git' \
  --exclude '__pycache__' \
  --exclude '.venv' \
  --exclude '.vscode' \
  --exclude 'kashiwaDemo' \
  --exclude '*.pyc' \
  --exclude '.DS_Store' \
  --exclude 'node_settings.json' \
  --exclude 'identity/journal.md' \
  --exclude 'identity/turn_log.jsonl' \
  --exclude 'community/notes.md' \
  --exclude 'sensors/*.md' \
  "$SRC" "$DEST"

# Restart the services so the Pi always runs the pushed code.
# (No-op with a hint if the systemd units aren't installed yet.)
ssh "$PI_HOST" '
  sudo -n systemctl restart solarturtle-web 2>/dev/null && echo "solarturtle-web restarted." \
    || echo "(solarturtle-web service not installed — run: sudo bash install_autostart.sh — or restart web_server.py by hand)"
  if systemctl list-unit-files | grep -q solarturtle-voice; then
    sudo -n systemctl restart solarturtle-voice 2>/dev/null && echo "solarturtle-voice restarted."
  fi
'

echo "Done."
