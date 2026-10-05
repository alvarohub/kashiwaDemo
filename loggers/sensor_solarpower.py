#!/usr/bin/env python3
"""Dummy solar-power sensor logger (Level 0) — PLACEHOLDER.

Simulates a solar panel + battery so the architecture can be developed and
tested before the real hardware (INA219 on I2C, or similar) is wired up.
Replace the _read() function with actual hardware reads later.

Usage:  python3 loggers/sensor_solarpower.py          (append one reading)
Runs:   under sensors_boot.py (sensors.json registry), interval=300s — or by
        hand for a fresh reading
"""

from __future__ import annotations

import math
import random
import sys
import time
from pathlib import Path

BASE = Path(__file__).parent.parent
SENSOR_FILE = BASE / "sensors" / "sensor_solarpower.md"
MAX_LINES = 120

HEADER = """# sensor: solarpower
# description: Solar panel output + battery state of the node. DUMMY DATA —
#   simulated day/night curve until the INA219 (I2C) is wired up.
# location: the node's solar panel
# units: panel_v=V, battery_pct=%, charging=0/1
# logger: loggers/sensor_solarpower.py via cron, interval=300s
# queryable: yes — run this file directly for a fresh reading
# updated: {updated}
"""


def _read() -> tuple[float, float, int]:
    """Simulate panel voltage and battery % with a day/night sine + noise."""
    hour = time.localtime().tm_hour + time.localtime().tm_min / 60
    daylight = max(0.0, math.sin((hour - 6) / 12 * math.pi))  # 6h→18h
    panel_v = round(18.0 * daylight + random.uniform(-0.3, 0.3), 2)
    charging = 1 if panel_v > 13.0 else 0
    battery_pct = round(min(100.0, max(20.0, 60 + 30 * daylight + random.uniform(-2, 2))), 1)
    return panel_v, battery_pct, charging


def main() -> None:
    SENSOR_FILE.parent.mkdir(exist_ok=True)
    panel_v, battery_pct, charging = _read()
    line = f"{time.strftime('%Y-%m-%d %H:%M')}  panel_v={panel_v}V  battery={battery_pct}%  charging={charging}"

    if SENSOR_FILE.exists():
        lines = SENSOR_FILE.read_text().splitlines()
        head = [l for l in lines if l.startswith("#")]
        body = [l for l in lines if l and not l.startswith("#")]
        body = (body + [line])[-MAX_LINES:]
        SENSOR_FILE.write_text("\n".join(head) + "\n" + "\n".join(body) + "\n")
    else:
        SENSOR_FILE.write_text(
            HEADER.format(updated=time.strftime("%Y-%m-%d %H:%M:%S")) + "\n" + line + "\n"
        )
    print(line)


if __name__ == "__main__":
    main()
