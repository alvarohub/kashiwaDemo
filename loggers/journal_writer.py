#!/usr/bin/env python3
"""The kami's mechanical journal — writes today's entry into identity/journal.md.

Run from cron once a day (e.g. 23:55), or by hand:
    python3 loggers/journal_writer.py

NO LLM involved — the tiny model can't be trusted to summarize itself yet.
This is deliberately mechanical: per-sensor min/max/mean for the day, plus
turn counts and question themes (top words) from the status history.
When a bigger model exists up the hierarchy, IT can read the journal and
write the *reflective* layer (patterns across days). The format is designed
for that handoff.

Journal entry format (one block per day, newest at the end):
    ## 2026-09-27
    - world: cpu 12–88% (avg 41), temp 48–61C; solar: charged 14:00–17:30
    - talk: 23 questions; topics: water, turtles, wifi
"""

from __future__ import annotations

import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

BASE = Path(__file__).parent.parent
JOURNAL = BASE / "identity" / "journal.md"
TURN_LOG = BASE / "identity" / "turn_log.jsonl"
SENSORS_DIR = BASE / "sensors"

STOPWORDS = set("the a an and or of to in is it on for with how what who are you "
                "your my i we they this that do does did can could would s t "
                "just really know think like".split())


def summarize_sensor_file(path: Path) -> str | None:
    """Crude min/max over 'key=value' pairs found in today's lines."""
    today = time.strftime("%Y-%m-%d")
    try:
        lines = [l for l in path.read_text().splitlines() if l.startswith(today)]
    except OSError:
        return None
    if not lines:
        return None
    values: dict[str, list[float]] = {}
    for l in lines:
        for key, val in re.findall(r"(\w+)=([\d.]+)", l):
            values.setdefault(key, []).append(float(val))
    if not values:
        return None
    bits = []
    for k, vs in values.items():
        bits.append(f"{k} {min(vs):.0f}–{max(vs):.0f} (avg {sum(vs)/len(vs):.0f})")
    return f"{path.stem.replace('sensor_', '')}: " + "; ".join(bits)


def main() -> None:
    today = time.strftime("%Y-%m-%d")
    world_bits = []
    for f in sorted(SENSORS_DIR.glob("sensor_*.md")):
        s = summarize_sensor_file(f)
        if s:
            world_bits.append(s)

    if not JOURNAL.exists():
        JOURNAL.parent.mkdir(exist_ok=True)
        JOURNAL.write_text(
            "# Journal — the node's own continuity\n\n"
            "# description: One short block per day, written mechanically by\n"
            "#   loggers/journal_writer.py (cron 23:55). The world half comes\n"
            "#   from sensor logs; the talk half from conversation stats.\n"
            "#   A bigger model up the hierarchy may later read this and write\n"
            "#   the reflective layer (patterns across days).\n\n"
        )
    elif f"## {today}" in JOURNAL.read_text():
        print(f"journal already has an entry for {today} — skipping")
        return

    # Conversation stats from the machine-readable turn log.
    talk = "no conversations recorded"
    if TURN_LOG.exists():
        today_qs: list[str] = []
        n_ok = 0
        for line in TURN_LOG.read_text().splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("ts", "").startswith(today):
                if rec.get("q"):
                    today_qs.append(rec["q"])
                if rec.get("ok"):
                    n_ok += 1
        if today_qs:
            words = Counter(
                w for q in today_qs
                for w in re.findall(r"[a-z']+", q.lower()) if w not in STOPWORDS
            )
            topics = ", ".join(w for w, _ in words.most_common(4))
            talk = f"{len(today_qs)} questions ({n_ok} answered); topics: {topics}"

    entry = f"\n## {today}\n"
    if world_bits:
        entry += "- world: " + "; ".join(world_bits) + "\n"
    else:
        entry += "- world: (no sensor data today)\n"
    entry += f"- talk: {talk}\n"

    with JOURNAL.open("a") as f:
        f.write(entry)
    print(f"journal entry written for {today}")


if __name__ == "__main__":
    main()
