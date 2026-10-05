#!/usr/bin/env python3
"""Example Level-0 sensor logger for the kami node.

Runs periodically under sensors_boot.py (the sensors.json registry
supervisor), or by hand for a fresh reading. Appends one human-readable
line to sensors/sensor_pivitals.md, which starts with a metadata header
(description, units, logger config) that the LLM reads. Keep that
convention when you write your own loggers — see SENSORS.md.

This one logs the Pi's own vitals (CPU/RAM/temp) as a stand-in for real
sensors. Real loggers (BME280 on I2C, LoRa/meshtastic weather stations, ...)
follow the same format: header + recent observations.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))  # for config, hardware_monitor

import config
from hardware_monitor import HardwareMonitor

BASE = Path(__file__).parent.parent
SENSORS_DIR = BASE / "sensors"
COMMUNITY_DIR = BASE / "community"
SENSOR_FILE = SENSORS_DIR / "sensor_pivitals.md"
MAX_LINES = 60  # keep the files short — the LLM reads them whole

HEADER = """# Report: sensor_pivitals
# description: The node's own health — CPU load, memory, chip temperature.
#   Stand-in for real environmental sensors until they are wired up.
# logger_config: interval=60s (sensors.json), retains last ~60 samples
# location: the kami node itself (this node)
# units: cpu=%%, ram=%%, temp=degC
# tweakable: interval (sensors.json), MAX_LINES (this file's length)
# updated: {updated}
"""


def main() -> None:
    SENSORS_DIR.mkdir(exist_ok=True)
    snap = HardwareMonitor().read_snapshot()
    line = (
        f"{time.strftime('%Y-%m-%d %H:%M')}  "
        f"cpu={snap.cpu_percent if snap.cpu_percent is not None else '--'}%  "
        f"ram={snap.ram_percent if snap.ram_percent is not None else '--'}%  "
        f"temp={snap.temperature_c if snap.temperature_c is not None else '--'}C"
    )

    # Append to the per-sensor file (single source of truth).
    if SENSOR_FILE.exists():
        slines = SENSOR_FILE.read_text().splitlines()
        sbody = [l for l in slines if l and not l.startswith("#")]
        shead = [l for l in slines if l.startswith("#")]
        sbody = (sbody + [line])[-MAX_LINES:]
        SENSOR_FILE.write_text("\n".join(shead) + "\n" + "\n".join(sbody) + "\n")
    else:
        SENSOR_FILE.write_text(HEADER + "\n" + line + "\n")

    print(f"wrote {SENSOR_FILE}")


if __name__ == "__main__":
    main()
