#!/usr/bin/env bash
# install.sh — one-shot setup for a fresh Raspberry Pi. Run from inside this
# folder (the repository):
#
#     cd ~/solarturtle && bash install.sh
#
# Installs: system packages (apt), Ollama, and the model named in config.py.
# Does NOT enable autostart — that is a separate, optional step:
#
#     sudo bash install_autostart.sh
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

if [ ! -f config.py ] || [ ! -f web_server.py ]; then
  echo "This does not look like the code folder (config.py not found here)."
  echo "Get the code first:"
  echo "  git clone https://github.com/alvarohub/kashiwaDemo.git ~/solarturtle"
  echo "  cd ~/solarturtle && bash install.sh"
  exit 1
fi

if [ "$(uname -m)" != "aarch64" ]; then
  echo "Warning: 64-bit ARM (aarch64) expected — Ollama has no 32-bit ARM build."
fi

echo
echo "== 1/3  System packages =="
sudo apt-get update
# python3-tk is only needed for the optional fullscreen GUI.
sudo apt-get install -y git curl python3-requests python3-psutil \
  python3-flask python3-waitress python3-venv python3-tk

echo
echo "== 2/3  Ollama =="
if command -v ollama >/dev/null 2>&1; then
  echo "Already installed: $(ollama --version 2>/dev/null | head -1)"
else
  curl -fsSL https://ollama.com/install.sh | sh
fi
sudo systemctl enable --now ollama

# Wait until the Ollama daemon answers (it may still be starting).
for _ in $(seq 1 15); do
  curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && break
  sleep 1
done

echo
echo "== 3/3  Model (name taken from config.py) =="
MODEL="$(python3 -c 'import config; print(config.MODEL_NAME)')"
if ollama list 2>/dev/null | awk '{print $1}' | grep -qx "$MODEL"; then
  echo "Already downloaded: $MODEL"
else
  echo "Downloading $MODEL — a few minutes on a decent connection..."
  ollama pull "$MODEL"
fi

echo
echo "== Done =="
cat <<'EOM'
Run the node now:               ./kami
Phone chat:                     http://<this-pi>:8080
Start on boot (optional):       sudo bash install_autostart.sh
Wi-Fi AP + portal (optional):   sudo bash ap_up.sh  &&  sudo bash portal_up.sh
Voice, USB mic (optional):      bash setup_voice.sh
EOM
