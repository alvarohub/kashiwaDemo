"""Reads the node's plain-text memory into a prompt-ready digest.

Three category folders, all plain markdown readable by humans and the LLM:

- identity/   — who the kami is (identity.md) + its journal
- sensors/    — one file per sensor + index.md (the cross-sensor dashboard)
- community/  — notes.md (people's 📝 notes: suggestions, polls, comments)

Deliberately simple: whole files, truncated per-file, newest content kept.
No MCP server, no database. (A full MCP interface, e.g.
@modelcontextprotocol/server-filesystem, can replace this later without
changing the file formats.)
"""

from __future__ import annotations

from pathlib import Path

BASE = Path(__file__).parent
IDENTITY_DIR = BASE / "identity"
SENSORS_DIR = BASE / "sensors"
COMMUNITY_DIR = BASE / "community"

MAX_CHARS_PER_FILE = 900  # keep the prompt small; files are short by design
MAX_TOTAL_CHARS = 4000

# The three memory categories, toggleable per request from the web UI and/or
# force-enabled with slash keywords (/identity, /sensors, /community).
CATEGORIES = ("identity", "sensors", "community")


def _digest_dir(directory: Path, skip: set[str] = frozenset()) -> list[str]:
    """Digest one directory: header + newest content per file."""
    parts: list[str] = []
    if not directory.is_dir():
        return parts
    for path in sorted(directory.glob("*.md")):
        if path.name in skip:
            continue
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not text:
            continue
        if len(text) > MAX_CHARS_PER_FILE:
            head, _, tail = text.partition("\n\n")
            keep = MAX_CHARS_PER_FILE - len(head) - 40
            text = head + "\n[...older entries omitted...]\n" + tail[-keep:]
        parts.append(f"--- {directory.name}/{path.name} ---\n{text}")
    return parts


# Machine field names -> words a small model can map a question onto.
FIELD_WORDS = {
    "temp": "temperature",
    "cpu": "cpu load",
    "ram": "ram usage",
    "panel_v": "panel voltage",
    "battery": "battery level",
    "charging": "charging",
}


def _latest_line(path: Path, lines: list[str]) -> str:
    """Render the sensor's most recent data line as natural language, e.g.
    "temperature: 51.8C · cpu load: 0.0% · ram usage: 17.7%"."""
    toks = lines[-1].split()
    ts = f"{toks[0]} {toks[1]}" if len(toks) > 1 else toks[0]
    parts = []
    for token in toks[2:]:
        key, _, val = token.partition("=")
        parts.append(f"{FIELD_WORDS.get(key, key)}: {val}")
    return f"latest reading ({ts}): " + " · ".join(parts)


def _sensor_digest() -> list[str]:
    """One clean "latest reading" per sensor.

    The raw sensor files are append-only logs (one line per minute), and a 1B
    model asked "what's the temperature?" will pick a random line — or invent
    a number — rather than re-parse sixty timestamped lines. So instead of the
    log, give it the sensor's description, its units, and its LATEST line,
    with machine field names spelled out in words.
    index.md is skipped: its "Current state" summary is not written yet and
    its "(No sensor data yet)" actively misleads the model.
    """
    parts: list[str] = []
    if not SENSORS_DIR.is_dir():
        return parts
    for path in sorted(SENSORS_DIR.glob("*.md")):
        if path.name == "index.md":
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        meta = [ln for ln in lines
                if ln.startswith("# description:") or ln.startswith("# units:")]
        data = [ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")]
        if not data:
            continue
        parts.append(
            f"--- sensors/{path.name} ---\n"
            + "\n".join(meta)
            + "\n" + _latest_line(path, data)
        )
    return parts


def read_reports(categories: set[str] | None = None) -> str:
    """Return the digest for the given categories, or "" if none active.

    categories ⊆ {"identity", "sensors", "community"}; None means all on.
    """
    if categories is None:
        categories = set(CATEGORIES)

    sections: list[str] = []

    if "identity" in categories:
        identity = _digest_dir(IDENTITY_DIR, skip={"journal.md"})
        if identity:
            sections.append("## Who you are\n" + "\n\n".join(identity))

    if "sensors" in categories:
        sensors = _sensor_digest()
        if sensors:
            sections.append("## Sensor readings (latest)\n" + "\n\n".join(sensors))

    if "community" in categories:
        reports = _digest_dir(COMMUNITY_DIR)
        if reports:
            sections.append("## Community notes\n" + "\n\n".join(reports))

    digest = "\n\n".join(sections)
    return digest[:MAX_TOTAL_CHARS]
