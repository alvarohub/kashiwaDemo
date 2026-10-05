# Sensors — how they work, and how to add one

The node "senses" through small logger scripts that append one line of
readings to a file in `sensors/`, at a fixed interval. No database, no
framework — the files ARE the interface (the LLM reads them; so can you).

## The pipeline

```
  sensors.json ──► sensors_boot.py ──spawns──► loggers/sensor_x.py  (every N s)
  (registry)       (run at boot or          one supervisor per            │
                    by hand; systemd        active sensor)                ▼
                    service if installed)                        sensors/sensor_x.md
                                                                 (append one line)
                                                                        │
                                          reports_reader.py supplies the LLM with
                                          description + units + the LATEST line
                                          (never the whole log)
```

See what would run:  `python3 sensors_boot.py --status`

## Conventions (what makes a good logger)

- One file per sensor in `sensors/`, named after the logger.
- The file starts with a metadata HEADER — `# description:` and `# units:`
  are what the LLM reads — followed by one human-readable line per sample:

      2026-10-05 17:07  cpu=0.0%  ram=81.2%  temp=51.3C

- Keep the file short: cap the number of lines; older lines drop off.
- Any source works: GPIO, I2C (BME280, INA219…), a web API, a serial device.
  Loggers are Python scripts; internally they may call any shell tool.
- Nice touch: the logger can also be run by hand for a fresh reading.

## How the LLM reads sensors

`reports_reader.py` gives the model, per sensor: the description, the units,
and the most recent line. The whole log would confuse a small model (it
picks a random old line, or invents numbers). Everything else stays on disk.
The "sensors" memory category is off by default — ask "what's the
temperature?" and the keyword turns it on, or force it with `/sensors ...`.

## Example: add your own sensor

Say you wire a light sensor on I2C. Three steps:

### 1. Write the logger — `loggers/sensor_light.py`

```python
#!/usr/bin/env python3
"""Light sensor logger — appends one line to sensors/sensor_light.md."""
import time
from pathlib import Path

BASE = Path(__file__).parent.parent
SENSOR_FILE = BASE / "sensors" / "sensor_light.md"
MAX_LINES = 120

HEADER = """# sensor: light
# description: Ambient light level at the installation.
# location: north side of the enclosure
# units: lux
# logger: loggers/sensor_light.py, interval=60s
# updated: {updated}
"""

def read_lux() -> float:
    # TODO: read your hardware here (I2C / GPIO / serial / HTTP ...)
    return 0.0

def main() -> None:
    SENSOR_FILE.parent.mkdir(exist_ok=True)
    line = f"{time.strftime('%Y-%m-%d %H:%M')}  lux={read_lux():.1f}"
    if SENSOR_FILE.exists():
        lines = SENSOR_FILE.read_text().splitlines()
        header = [l for l in lines if l.startswith("#")]
        body = [l for l in lines if l and not l.startswith("#")]
        body = (body + [line])[-MAX_LINES:]
        SENSOR_FILE.write_text("\n".join(header) + "\n" + "\n".join(body) + "\n")
    else:
        SENSOR_FILE.write_text(
            HEADER.format(updated=time.strftime("%Y-%m-%d %H:%M")) + "\n" + line + "\n")
    print(f"wrote {SENSOR_FILE}")

if __name__ == "__main__":
    main()
```

(`loggers/sensor_pivitals.py` is the same pattern in full, if you prefer a
copy-and-edit start.)

### 2. Register it — add to `sensors.json`

```json
    {
      "id": "light",
      "name": "Ambient light (north side)",
      "type": "environment",
      "logger": "loggers/sensor_light.py",
      "log_file": "sensors/sensor_light.md",
      "interval_seconds": 60,
      "active": true,
      "params": { "max_lines": 120 }
    }
```

### 3. Start it and check

    sudo systemctl restart solarturtle-sensors     # or just reboot
    python3 sensors_boot.py --status               # should list "light  ACTIVE"
    tail -f sensors/sensor_light.md                # a new line every 60 s

Then ask the chat: "how bright is it?" — the sensors category switches on by
itself (or use `/sensors how bright is it?`).

That is the whole system: write a small script, register it, and the node
starts noticing your hardware.
