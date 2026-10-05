#!/usr/bin/env bash
# Stop the SolarTurtle node processes.
# By default Ollama is left running (the model stays warm in RAM).
#   ./stop_kami.sh           stop web + voice + sensors
#   ./stop_kami.sh --all     also stop the Ollama LLM server
# Run ON THE PI.
set -euo pipefail

echo "Stopping SolarTurtle…"
sudo systemctl stop solarturtle-web solarturtle-voice solarturtle-sensors

if [[ "${1:-}" == "--all" ]]; then
  echo "Stopping Ollama too…"
  sudo systemctl stop ollama
else
  echo "(Ollama left running — pass --all to stop it as well)"
fi
echo
echo "✔ stopped — restart with:  ./start_kami.sh"

