#!/usr/bin/env python3
"""Microphone calibration + STT test for the kami node (Phase 3, step 1-2).

Run ON THE PI (with the voice venv for the STT part):
    python3 voice/mic_setup.py                  # full interactive calibration
    python3 voice/mic_setup.py --check          # non-interactive health check
    ~/voice-venv/bin/python voice/mic_setup.py --transcribe /tmp/kami_test.wav

What it does (all logged to sensors/mic_calibration.md so we never lose the
tweaks):
  1. Finds the ReSpeaker card automatically (looks for "ReSpeaker"/"ArrayUAC").
  2. Optionally sets capture gain via amixer.
  3. Records a test clip, measures peak/RMS levels, judges the level.
  4. (Optional, needs voice-venv) Transcribes the clip with faster-whisper.

Hardware notes (learned on our test node, 2026-09-27):
  - ReSpeaker 4 Mic Array enumerates as card "ArrayUAC10" (UAC1.0 firmware —
    no hardware AEC/DOA in this variant, but beamforming is on).
  - Default gain is shy: speech peaks ~4000; target >5000. Fix:
    `amixer -c <card> sset PCM 90%` (or alsamixer -c <card>).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
import wave
from pathlib import Path

BASE = Path(__file__).parent.parent
CALIB_LOG = BASE / "sensors" / "mic_calibration.md"
DEFAULT_SECONDS = 5
RATE = 16000  # whisper expects 16 kHz mono 16-bit
TARGET_PEAK = 5000


def find_capture_card() -> tuple[int, int] | None:
    """Return (card, device) of the ReSpeaker, else None."""
    try:
        out = subprocess.run(
            ["arecord", "-l"], capture_output=True, text=True, check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    # "card 3: ArrayUAC10 [ReSpeaker 4 Mic Array (UAC1.0)], device 0: ..."
    for m in re.finditer(r"card (\d+): (\w+) \[([^\]]+)\], device (\d+)", out):
        card, _code, name, dev = int(m.group(1)), m.group(2), m.group(3), int(m.group(4))
        if "respeaker" in name.lower() or "array" in name.lower():
            return card, dev
    return None


def set_gain(card: int, percent: int) -> str:
    """Set PCM capture gain; returns the amixer output."""
    r = subprocess.run(
        ["amixer", "-c", str(card), "sset", "PCM", f"{percent}%"],
        capture_output=True, text=True,
    )
    return r.stdout.strip() or r.stderr.strip()


def record(card: int, dev: int, seconds: int, path: Path) -> None:
    subprocess.run(
        ["arecord", "-D", f"plughw:{card},{dev}", "-f", "S16_LE",
         "-r", str(RATE), "-c", "1", "-d", str(seconds), str(path)],
        check=True,
    )


def measure(path: Path) -> tuple[int, float]:
    """Peak amplitude and RMS of a 16-bit mono WAV."""
    with wave.open(str(path)) as w:
        frames = w.readframes(w.getnframes())
    samples = [
        int.from_bytes(frames[i:i + 2], "little", signed=True)
        for i in range(0, len(frames), 2)
    ]
    if not samples:
        return 0, 0.0
    peak = max(abs(s) for s in samples)
    rms = (sum(s * s for s in samples) / len(samples)) ** 0.5
    return peak, rms


def log_result(text: str) -> None:
    CALIB_LOG.parent.mkdir(exist_ok=True)
    if not CALIB_LOG.exists():
        CALIB_LOG.write_text(
            "# sensor: mic_calibration\n"
            "# description: Microphone setup history for the kami node\n"
            "#   (device, gain, levels, STT results). Appended by\n"
            "#   voice/mic_setup.py so hardware tweaks are never lost.\n"
            "# updated: (see entries)\n\n"
        )
    with CALIB_LOG.open("a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M')}  {text}\n")


def transcribe(path: Path, model_size: str = "tiny") -> None:
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        sys.exit("faster-whisper not in this interpreter — run with "
                 "~/voice-venv/bin/python")
    print(f"loading whisper '{model_size}' (first run downloads the model)...")
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    t0 = time.perf_counter()
    segments, info = model.transcribe(str(path))
    dt = time.perf_counter() - t0
    print(f"language: {info.language} ({info.language_probability:.0%}), "
          f"transcribed in {dt:.1f}s")
    text = " ".join(s.text for s in segments).strip()
    print(f"transcript: {text!r}")
    log_result(f"STT test: model={model_size} lang={info.language} "
               f"time={dt:.1f}s text={text!r}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="non-interactive: detect card + report levels")
    ap.add_argument("--gain", type=int, metavar="PCT",
                    help="set PCM capture gain (e.g. 90) and exit")
    ap.add_argument("--seconds", type=int, default=DEFAULT_SECONDS)
    ap.add_argument("--transcribe", metavar="WAV",
                    help="transcribe an existing file and exit")
    ap.add_argument("--model", default="tiny", help="whisper model size")
    args = ap.parse_args()

    if args.transcribe:
        transcribe(Path(args.transcribe), args.model)
        return

    found = find_capture_card()
    if not found:
        sys.exit("No ReSpeaker found. Check `arecord -l`.")
    card, dev = found
    print(f"ReSpeaker: card {card}, device {dev}")

    if args.gain is not None:
        print(set_gain(card, args.gain))
        log_result(f"gain set to {args.gain}% (card {card})")
        if args.check:
            return

    clip = Path("/tmp/kami_mic_test.wav")
    if args.check:
        record(card, dev, args.seconds, clip)
    else:
        input(f"Press Enter, then SPEAK for {args.seconds} seconds... ")
        record(card, dev, args.seconds, clip)

    peak, rms = measure(clip)
    verdict = ("GOOD — clear speech" if peak >= TARGET_PEAK
               else f"LOW — raise gain: python3 voice/mic_setup.py --gain 90")
    print(f"peak={peak} rms={rms:.0f}  → {verdict}")
    log_result(f"levels: peak={peak} rms={rms:.0f} (card {card}) — {verdict}")
    print(f"clip saved: {clip}")
    print(f"next: ~/voice-venv/bin/python voice/mic_setup.py --transcribe {clip}")


if __name__ == "__main__":
    main()
