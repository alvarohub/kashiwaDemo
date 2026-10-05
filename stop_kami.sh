#!/usr/bin/env bash
# Stop the SolarTurtle node processes.
# By default Ollama is left running (the model stays warm in RAM).
#   ./stop_kami.sh           stop web + voice + sensors
#   ./stop_kami.sh --all     also stop the Ollama LLM server
# Run ON THE PI.
set -euo pipefail

echo "Stopping SolarTurtle…"
sudo systemctl stop solarturtle-web solarturtle-voice solarturtle-sensors

# Also sweep leftover processes from earlier manual runs — systemd only stops
# its own cgroup; a hand-started ./kami or a half-dead service can leave
# web / sensors / loggers / voice / arecord processes behind.
pkill -f "(python|python3) .*web_server\.py" 2>/dev/null || true
pkill -f "(python|python3) .*sensors_boot\.py" 2>/dev/null || true
pkill -f "(python|python3) .*loggers/sensor_" 2>/dev/null || true
pkill -f "(python|python3) .*voice/talk\.py" 2>/dev/null || true
pkill -f "arecord.*kami_" 2>/dev/null || true

if [[ "${1:-}" == "--all" ]]; then
  echo "Stopping Ollama too…"
  sudo systemctl stop ollama
else
  echo "(Ollama left running — pass --all to stop it as well)"
fi
echo
echo "✔ stopped — restart with:  ./start_kami.sh"

