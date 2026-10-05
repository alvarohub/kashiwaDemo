#!/usr/bin/env bash
# setup_voice.sh — voice-loop dependencies (run ON THE PI, as your user):
#
#     bash setup_voice.sh
#
# Creates ~/voice-venv (faster-whisper + the VAD runtime) and downloads the
# Silero VAD model into models/. Afterwards use the microphone button on the
# chat page (with autostart, the voice service starts on boot and idles until
# the mic is enabled from the page).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

if [ "$(id -u)" = "0" ]; then
  echo "Run as your normal user (not root). sudo is used where needed."
  exit 1
fi

echo "== 1/3  python3-venv =="
sudo apt-get install -y python3-venv

echo "== 2/3  ~/voice-venv (downloads a few hundred MB) =="
python3 -m venv "$HOME/voice-venv"
"$HOME/voice-venv/bin/pip" install --quiet --upgrade pip
"$HOME/voice-venv/bin/pip" install --quiet faster-whisper onnxruntime numpy requests psutil

echo "== 3/3  Silero VAD model =="
mkdir -p models
if [ ! -f models/silero_vad.onnx ]; then
  curl -L -o models/silero_vad.onnx \
    https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.onnx
fi

echo
echo "Voice ready. Try:  ~/voice-venv/bin/python voice/talk.py --serve"
echo "(the first transcription also downloads the whisper 'tiny' model)"
