#!/usr/bin/env python3
"""Spawn the sensor loggers listed in sensors.json (run at boot, or by hand).

Each active registry entry with an interval becomes a small supervisor
process that runs its logger every `interval_seconds`, forever. Crashes are
logged and retried on the next tick — a dead logger never kills the others.

    python3 sensors_boot.py            # run all active loggers (foreground)
    python3 sensors_boot.py --status   # print registry + which would run

 systemd unit comes with set_autostart.sh on (solarturtle-sensors.service).
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

BASE = Path(__file__).parent
REGISTRY = BASE / "sensors.json"


def load_registry() -> dict:
    return json.loads(REGISTRY.read_text())


def supervisor_loop(sensor: dict) -> None:
    """Run one logger forever at its interval. Never raises."""
    sid = sensor["id"]
    interval = sensor["interval_seconds"]
    logger = BASE / sensor["logger"]
    while True:
        t0 = time.time()
        try:
            r = subprocess.run(
                [sys.executable, str(logger)],
                capture_output=True, text=True, timeout=interval,
            )
            if r.returncode != 0:
                print(f"[{sid}] logger error: {r.stderr.strip()[:200]}", flush=True)
        except Exception as exc:
            print(f"[{sid}] supervisor caught: {exc}", flush=True)
        elapsed = time.time() - t0
        time.sleep(max(1.0, interval - elapsed))


def main() -> None:
    registry = load_registry()
    sensors = registry["sensors"]

    if "--status" in sys.argv:
        for s in sensors:
            state = "ACTIVE" if s.get("active") else "off"
            print(f"{s['id']:16} {state:7} every {s.get('interval_seconds')}s "
                  f"-> {s['log_file']}  ({s['name']})")
        return

    threads = []
    for s in sensors:
        if not s.get("active") or not s.get("interval_seconds"):
            continue
        t = threading.Thread(
            target=supervisor_loop, args=(s,), name=f"sensor-{s['id']}", daemon=True,
        )
        t.start()
        threads.append(t)
        print(f"spawned logger: {s['id']} (every {s['interval_seconds']}s)", flush=True)

    if not threads:
        print("No active periodic sensors in sensors.json — nothing to do.")
        return
    print(f"{len(threads)} logger(s) running. Ctrl+C to stop.")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\nstopping supervisors")


if __name__ == "__main__":
    main()
