"""Node-wide settings shared by EVERY frontend (web, voice, console, GUI).

Why this file exists
--------------------
Each frontend is a separate process with its own LLMManager *client*. The
model is shared (it lives in Ollama), but until now the *settings* were not:
the web page sent its memory toggles with each question, while voice/console
sent nothing and silently got "all memory on". Same question, same model,
two different answers and two very different speeds.

These settings belong to the NODE, not to whichever process happens to be
talking. So they live where all the other shared state lives: in a file.
Every frontend reads it at the start of each turn; the web UI writes it.
Flip a toggle on your phone and the voice loop obeys too.

    node_settings.json   {"memory": ["identity"], "model": "llama3.2:1b"}

Deliberately re-read per turn (a few microseconds) rather than cached, so a
change takes effect without restarting anything.

There is a second, less obvious reason to share them. Ollama caches the
evaluated prompt *prefix*, and only one at a time per model: two frontends
with two different system prompts evict each other's cache, so every switch
between the phone and the microphone pays a full re-evaluation (measured:
0.45 s → 5.1 s). One shared setting means one prefix, cached, for everybody.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

PATH = Path(__file__).parent / "node_settings.json"

# memory: which category folders enter the system prompt ([] = bare prompt).
# identity is on by default: it is small (~1 kB) and it is what makes this a
# kami rather than a generic assistant. Measured on the Pi 5: bare 0.4 s to
# first token, identity 5.2 s the FIRST time and 0.45 s afterwards (Ollama
# caches the prompt prefix). sensors+community push it to ~18 s, so they
# stay off until asked for.
# model: None = whatever config.MODEL_NAME says.
# voice.enabled: whether the wake word is being listened for (on/off). The
#        speak button works regardless of this flag.
# voice.wake_word: the word that arms the kami.
# voice.vad: gate the microphone with Silero VAD before whisper sees it.
#        Turning it off is a debug escape hatch — whisper will hallucinate on
#        silence, which is exactly what the gate prevents.
# listen_request: a timestamp. Bumping it arms exactly one capture; that is
#        how the page's "Speak" button reaches across to the voice process.
# stop_request: a timestamp. Bumping it ends the current capture early; that
#        is how the page's "Stop" button reaches across mid-recording.
DEFAULTS: dict[str, object] = {
    "memory": ["identity"],
    "model": None,
    "voice": {"enabled": False, "wake_word": "wake", "vad": True},
    "listen_request": 0.0,
    "stop_request": 0.0,
}


def _sanitize(data: object) -> dict:
    """Validated settings: anything missing or malformed falls back to DEFAULTS.

    Used on the way in AND on the way out, so a bad value can never be
    written to disk and then quietly masked on every later read.
    """
    out = json.loads(json.dumps(DEFAULTS))  # deep copy, no shared nested dict
    if not isinstance(data, dict):
        return out
    if isinstance(data.get("memory"), list):
        out["memory"] = [c for c in data["memory"] if isinstance(c, str)]
    if isinstance(data.get("model"), str) and data["model"]:
        out["model"] = data["model"]
    if isinstance(data.get("listen_request"), (int, float)):
        out["listen_request"] = float(data["listen_request"])
    if isinstance(data.get("stop_request"), (int, float)):
        out["stop_request"] = float(data["stop_request"])
    voice = data.get("voice")
    if isinstance(voice, dict):
        out["voice"]["enabled"] = bool(voice.get("enabled", False))
        if isinstance(voice.get("wake_word"), str) and voice["wake_word"].strip():
            out["voice"]["wake_word"] = voice["wake_word"].strip()
        if "vad" in voice:
            out["voice"]["vad"] = bool(voice["vad"])
    return out


def load() -> dict:
    """Current settings, falling back to DEFAULTS for anything missing."""
    try:
        return _sanitize(json.loads(PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return _sanitize(None)


def memory_categories() -> set[str]:
    """The shared memory toggles, as the set LLMManager expects."""
    return set(load()["memory"])  # type: ignore[arg-type]


def model() -> str | None:
    return load()["model"]  # type: ignore[return-value]


def save(**patch) -> dict:
    """Merge `patch` into the settings and write atomically."""
    current = load()
    for key, value in patch.items():
        if key not in DEFAULTS:
            continue
        if key == "voice" and isinstance(value, dict):
            current["voice"].update(value)
        else:
            current[key] = value
    current = _sanitize(current)
    tmp = PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, PATH)  # atomic: readers never see a half-written file
    return current


# --- voice heartbeat -------------------------------------------------------
# The voice loop is a separate process that may not even be running. It
# publishes what it is doing here; the page reads it and can say "offline"
# honestly instead of pretending the microphone is there.
HEARTBEAT = Path("/tmp/solarturtle_voice.json")
HEARTBEAT_STALE_S = 12.0

# The last thing whisper transcribed, sticky across states. Without it there
# is no way to tell "the microphone is deaf" from "the wake word didn't match".
_LAST_HEARD = {"text": "", "ts": 0.0}


def publish_voice_state(state: str, detail: str = "",
                        heard: str | None = None, vad: float = 0.0,
                        question: str = "", partial: str = "") -> None:
    if heard is not None:
        _LAST_HEARD["text"] = heard
        _LAST_HEARD["ts"] = time.time()
    try:
        tmp = HEARTBEAT.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "ts": time.time(),
            "state": state,
            "detail": detail,
            "heard": _LAST_HEARD["text"],
            "heard_ts": _LAST_HEARD["ts"],
            "vad": float(vad or 0.0),
            "question": question,
            "partial": partial,
        }), encoding="utf-8")
        os.replace(tmp, HEARTBEAT)
    except OSError:
        pass


def voice_state() -> dict:
    """{"state": ...} — "offline" if the voice process isn't heartbeating."""
    try:
        data = json.loads(HEARTBEAT.read_text(encoding="utf-8"))
        if time.time() - float(data.get("ts", 0)) < HEARTBEAT_STALE_S:
            return {"state": str(data.get("state", "?")),
                    "detail": str(data.get("detail", "")),
                    "heard": str(data.get("heard", "")),
                    "heard_ts": float(data.get("heard_ts", 0) or 0),
                    "vad": float(data.get("vad", 0) or 0),
                    "question": str(data.get("question", "")),
                    "partial": str(data.get("partial", ""))}
    except (OSError, ValueError, TypeError):
        pass
    return {"state": "offline", "detail": "", "heard": "", "heard_ts": 0,
            "vad": 0.0, "question": "", "partial": ""}

