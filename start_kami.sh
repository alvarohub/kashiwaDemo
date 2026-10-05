#!/usr/bin/env bash
# Start the SolarTurtle node processes: Ollama (if stopped), web, voice, sensors.
#   ./start_kami.sh
# Run ON THE PI.
set -euo pipefail

echo "Starting SolarTurtle…"
sudo systemctl start ollama
sudo systemctl start solarturtle-web solarturtle-voice solarturtle-sensors
sleep 2
echo "Status:"
systemctl is-active solarturtle-web solarturtle-voice solarturtle-sensors ollama || true
echo
echo "Note: if Ollama was fully stopped, the model reloads on the first question (~8 s)."
