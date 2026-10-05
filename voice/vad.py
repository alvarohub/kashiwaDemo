"""Silero VAD over ONNX Runtime, plus a speech-utterance capturer.

Why this exists
---------------
Whisper-tiny transcribes silence into random English ("Let's go to the next
video!", "don't forget to subscribe!"). Left unguarded, the wake loop was
transcribing 2 s of quiet room every few seconds: hallucinated turns, wasted
CPU, and a "transcribing…" indicator that flickered for no reason.

Silero VAD is a tiny (1.5 MB) neural voice-activity detector. It runs in
real time on the CPU and only says "there is speech here" — it does NOT
recognise words. So the loop becomes: stream the mic through VAD; when
speech starts and then stops, hand that speech to whisper. Whisper only ever
sees actual utterances, so it cannot hallucinate on silence.

No torch needed: the model is the standard silero_vad.onnx, run here with
onnxruntime (already in the voice venv). The model is looked up in a few
known places — the repo's models/ dir, or the copies already on the Pi.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import numpy as np
import onnxruntime

SAMPLE_RATE = 16000
WINDOW = 512                # samples per VAD frame (32 ms at 16 kHz)
FRAME_MS = int(WINDOW / SAMPLE_RATE * 1000)

MODEL_CANDIDATES = [
    Path(__file__).parent.parent / "models" / "silero_vad.onnx",
    Path("/home/admin/SPEECH_RECORD_ANALYSIS/models/silero-vad/src/silero_vad/data/silero_vad.onnx"),
    Path("/home/admin/.cache/torch/hub/snakers4_silero-vad_master/src/silero_vad/data/silero_vad.onnx"),
]


def _find_model() -> Path:
    for p in MODEL_CANDIDATES:
        if p.is_file():
            return p
    raise FileNotFoundError(
        "silero_vad.onnx not found. Download it to models/silero_vad.onnx, e.g.\n"
        "  curl -L -o models/silero_vad.onnx "
        "https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.onnx"
    )


class VAD:
    """Stateful Silero VAD over ONNX. Feed it 512-sample int16 frames."""

    def __init__(self, threshold: float = 0.5) -> None:
        opts = onnxruntime.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        self.session = onnxruntime.InferenceSession(str(_find_model()), sess_options=opts)
        self.threshold = threshold
        self.reset()

    def reset(self) -> None:
        # Single recurrent state [2, 1, 128] (h and c concatenated).
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        # The model expects 64 samples of the PREVIOUS frame prepended to the
        # current one (576 samples at 16 kHz). Without this the probabilities
        # come out near zero even for loud speech.
        self._context = np.zeros((1, 64), dtype=np.float32)

    def prob(self, chunk: bytes) -> float:
        x = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0
        x = x.reshape(1, -1)                       # [1, 512]
        xin = np.concatenate([self._context, x], axis=1)   # [1, 576]
        out = self.session.run(None, {
            "input": xin,
            "state": self._state,
            "sr": np.array(SAMPLE_RATE, dtype=np.int64),
        })
        self._state = out[1]
        self._context = xin[:, -64:]
        return float(np.asarray(out[0]).reshape(-1)[0])


def capture_utterance(card: int, dev: int, max_seconds: float = 15.0,
                      min_silence_ms: int = 600, auto_stop: bool = True,
                      vad_enabled: bool = True,
                      check=None, die_with_parent=None, on_vad=None) -> bytes | None:
    """Record ONE utterance.

    With VAD (vad_enabled=True): starts at speech onset; if auto_stop, ends
    after min_silence_ms of silence, otherwise runs until `check()` aborts
    (i.e. the user pressed Stop) or max_seconds.

    Without VAD (debug): records raw from the start until `check()` or
    max_seconds — whisper will then transcribe whatever arrived, silence
    included (which is exactly what the VAD gate exists to prevent).

    Returns int16 PCM bytes, or None if nothing usable / aborted.
    """
    if not vad_enabled:
        return _capture_raw(card, dev, max_seconds, check, die_with_parent)

    vad = VAD()
    proc = subprocess.Popen(
        ["arecord", "-D", f"plughw:{card},{dev}", "-f", "S16_LE",
         "-r", str(SAMPLE_RATE), "-c", "1", "-t", "raw"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        preexec_fn=die_with_parent,
    )
    frames = bytearray()
    speech = False
    silence_ms = 0
    start = time.time()
    last_check = start
    frame = WINDOW * 2   # bytes per 512-sample int16 frame

    try:
        while time.time() - start < max_seconds:
            chunk = proc.stdout.read(frame)
            if not chunk or len(chunk) < frame:
                break
            p = vad.prob(chunk)
            if on_vad:
                on_vad(p)
            if p >= vad.threshold:
                if not speech:
                    speech = True
                silence_ms = 0
                frames.extend(chunk)
            elif speech:
                frames.extend(chunk)
                if auto_stop:
                    silence_ms += FRAME_MS
                    if silence_ms >= min_silence_ms:
                        break
            if check and time.time() - last_check > 0.1:
                last_check = time.time()
                if check():
                    return None
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            proc.kill()

    if not speech or len(frames) < frame * 2:   # too little to transcribe
        return None
    return bytes(frames)


def _capture_raw(card: int, dev: int, max_seconds: float,
                 check=None, die_with_parent=None) -> bytes | None:
    """Record continuously from the start; stop on check() or max_seconds."""
    proc = subprocess.Popen(
        ["arecord", "-D", f"plughw:{card},{dev}", "-f", "S16_LE",
         "-r", str(SAMPLE_RATE), "-c", "1", "-t", "raw"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        preexec_fn=die_with_parent,
    )
    frames = bytearray()
    start = time.time()
    last_check = start
    frame = WINDOW * 2
    try:
        while time.time() - start < max_seconds:
            chunk = proc.stdout.read(frame)
            if not chunk:
                break
            frames.extend(chunk)
            if check and time.time() - last_check > 0.1:
                last_check = time.time()
                if check():
                    return None
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            proc.kill()
    return bytes(frames) if len(frames) >= frame * 2 else None
