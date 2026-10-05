#!/usr/bin/env python3
"""Push-to-talk / wake-word voice loop for the kami node (Phase 3, steps 3-4).

Run ON THE PI with the voice venv (needs faster-whisper + requests):
    ~/voice-venv/bin/python voice/talk.py --serve

`--serve` is the normal mode: the microphone does whatever the web page
says (off / wake word / press-to-talk), re-read from node_settings.json
every cycle. The two manual modes below still exist for debugging.

Controls (default: push-to-talk):
    Enter        — start recording; Enter again — stop; the kami answers.
    Ctrl+C       — exit.

Wake-word mode:
    ~/voice-venv/bin/python voice/talk.py --wake wake

    The mic listens continuously in short windows; when the whisper
    transcript contains the wake word (default "wake"), recording continues
    until a pause, and the remainder after the wake word becomes the
    question. Saying just "wake" wakes it for the NEXT utterance.

Design: this is a FRONTEND, exactly like console.py — it captures the
question (by voice instead of keyboard) and hands it to the same
LLMManager.stream_response(). The answer prints token by token, and the
turn is published to the status store, so the web dashboard sees it too.
"""

from __future__ import annotations

import argparse
import difflib
import errno
import fcntl
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))  # repo root on sys.path

import config
import node_settings
import queue
from llm_manager import LLMManager
from status_store import StatusStore
from voice.mic_setup import find_capture_card
from voice.vad import SAMPLE_RATE, capture_utterance

MODEL_SIZE = "tiny"
WHISPER: object | None = None  # lazy-loaded on first use

WAKE_SIMILARITY = 0.7

# --- one microphone, one owner ---------------------------------------------
# A USB mic can only be captured by one process. Two voice loops running at
# once means both get garbage and nothing ever works, with no error message
# to explain it (this happened on 2026-09-28: a hand-started loop and the
# systemd service were fighting). An flock is used rather than a PID file
# because the kernel releases it even if we are killed -9: no stale locks.
LOCK_PATH = Path("/tmp/solarturtle_voice.lock")
_LOCK_FILE = None  # kept open for the process lifetime, or the lock is lost
STOP = threading.Event()
CLIP_PREFIX = "kami_"


def _die_with_parent() -> None:
    """Ask the kernel to kill this child when its parent dies.

    Without it, `kill -9` on the voice loop leaves arecord running and
    holding the microphone forever — a silent, invisible failure: every
    later recording fails with EBUSY and nothing is ever transcribed.
    (Exactly what happened on 2026-09-28, for 52 minutes.)
    """
    try:
        import ctypes
        PR_SET_PDEATHSIG = 1
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(
            PR_SET_PDEATHSIG, signal.SIGKILL)
    except Exception:
        pass  # not Linux, or no libc: fall back to ordinary cleanup


def _kill_stray_recorders() -> None:
    """Kill leftover arecord processes from an earlier, crashed voice loop.
    Matched on our own temp-file prefix so nobody else's recording dies."""
    subprocess.run(["pkill", "-f", f"arecord.*{CLIP_PREFIX}"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def claim_microphone(takeover: bool = True) -> None:
    """Acquire the exclusive voice lock, stopping whoever else holds it."""
    global _LOCK_FILE
    _LOCK_FILE = LOCK_PATH.open("a+")
    for attempt in range(2):
        try:
            fcntl.flock(_LOCK_FILE, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _LOCK_FILE.seek(0)
            _LOCK_FILE.truncate()
            _LOCK_FILE.write(f"{os.getpid()}\n")
            _LOCK_FILE.flush()
            _kill_stray_recorders()
            for old in Path(tempfile.gettempdir()).glob(f"{CLIP_PREFIX}*"):
                old.unlink(missing_ok=True)
            return
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN):
                raise
            _LOCK_FILE.seek(0)
            holder = _LOCK_FILE.read().strip() or "?"
            if not takeover or attempt:
                sys.exit(f"Another voice loop is running (pid {holder}). "
                         f"Stop it first, or run with --takeover.")
            print(f"(another voice loop holds the microphone, pid {holder} "
                  f"— stopping it)")
            _terminate(holder)
    sys.exit("Could not claim the microphone.")


def _terminate(pid_text: str) -> None:
    try:
        pid = int(pid_text)
    except ValueError:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            return
        except PermissionError:
            sys.exit(f"pid {pid} belongs to another user — stop it by hand "
                     f"(sudo systemctl stop solarturtle-voice).")
        for _ in range(30):             # up to 3 s per signal
            time.sleep(0.1)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return


def _on_signal(_sig, _frame) -> None:
    STOP.set()


def split_on_wake(text: str, wake: str) -> tuple[bool, str]:
    """Was the wake word said, and what followed it?

    Fuzzy on purpose: whisper-tiny writes "kami" as commie, cami, khami,
    karmi... Exact matching made the wake word feel broken.
    """
    words = re.findall(r"[\w']+", text.lower())
    wake = wake.lower()
    for i, word in enumerate(words):
        if (word == wake
                or difflib.SequenceMatcher(None, word, wake).ratio() >= WAKE_SIMILARITY):
            return True, " ".join(words[i + 1:]).strip()
    return False, ""


def get_whisper():
    global WHISPER
    if WHISPER is None:
        from faster_whisper import WhisperModel
        print(f"(loading whisper '{MODEL_SIZE}' — first time takes a moment)")
        WHISPER = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")
    return WHISPER


def record_until_silence(card: int, dev: int, path: Path,
                         max_seconds: float = 15.0,
                         stop_check=None) -> bool:
    """Record until the speaker pauses (silence), stop_check() fires, or max.

    Records in 0.5 s chunks via a long-running arecord piped through a
    silence watcher: stops after SILENCE_AFTER_S below threshold, having
    heard speech at least once. Returns False if nothing usable captured.
    stop_check is polled every chunk so the page's Stop button can end the
    recording early.
    """
    SILENCE_AFTER_S = 1.6
    THRESHOLD = 800  # 16-bit amplitude; quiet room < ~300

    proc = subprocess.Popen(
        ["arecord", "-D", f"plughw:{card},{dev}", "-f", "S16_LE",
         "-r", "16000", "-c", "1", "-t", "raw", str(path) + ".raw"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        preexec_fn=_die_with_parent,
    )
    raw_path = Path(str(path) + ".raw")
    heard_speech = False
    silent_for = 0.0
    t0 = time.time()
    last_size = 0
    while time.time() - t0 < max_seconds:
        time.sleep(0.25)
        if stop_check and stop_check():
            break
        if not raw_path.exists():
            continue
        size = raw_path.stat().st_size
        chunk = size - last_size
        last_size = size
        if chunk > 0:
            with raw_path.open("rb") as f:
                f.seek(size - chunk)
                data = f.read(chunk)
            samples = [int.from_bytes(data[i:i+2], "little", signed=True)
                       for i in range(0, len(data) - 1, 2)]
            if samples and max(abs(s) for s in samples) > THRESHOLD:
                heard_speech = True
                silent_for = 0.0
            else:
                silent_for += 0.25
        if heard_speech and silent_for >= SILENCE_AFTER_S:
            break
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill(); proc.wait()

    # wrap the raw PCM in a WAV header so whisper can read it
    if not heard_speech or last_size < 2000:
        raw_path.unlink(missing_ok=True)
        return False
    import wave as _wave
    with raw_path.open("rb") as f:
        pcm = f.read()
    with _wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm)
    raw_path.unlink(missing_ok=True)
    return True


PAD_SECONDS = 0.5


def _padded_audio(path: Path):
    """Clip as float32, with silence glued to both ends.

    Whisper-tiny SWALLOWS THE FIRST WORD when speech starts at sample zero:
    "turtle, how warm is the pond?" transcribed as "How warm is the pond?" —
    which silently broke the wake word entirely. Padding fixes it (verified
    2026-09-28). An initial_prompt does NOT: it made matters worse.
    """
    import numpy as np
    import wave
    with wave.open(str(path)) as w:
        rate = w.getframerate()
        pcm = w.readframes(w.getnframes())
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    silence = np.zeros(int(rate * PAD_SECONDS), dtype=np.float32)
    return np.concatenate([silence, samples, silence])


def transcribe(path: Path) -> str:
    model = get_whisper()
    try:
        audio = _padded_audio(path)
    except Exception:
        audio = str(path)   # any surprise: fall back to the raw file
    segments, _info = model.transcribe(audio)
    return " ".join(s.text for s in segments).strip()


def transcribe_pcm(pcm: bytes) -> str:
    """Transcribe raw int16 PCM (from the VAD capturer), padded like the rest.

    Padding matters here too: the utterance starts at speech onset, and
    whisper-tiny clips the first word of an unpadded clip.
    """
    import numpy as np
    model = get_whisper()
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    silence = np.zeros(int(SAMPLE_RATE * PAD_SECONDS), dtype=np.float32)
    audio = np.concatenate([silence, samples, silence])
    segments, _info = model.transcribe(audio)
    return " ".join(s.text for s in segments).strip()


def new_clip() -> Path:
    """A fresh temp .wav path. mkstemp hands back an OPEN fd; closing it is
    not optional — leaking one per recording exhausts the process in minutes."""
    fd, name = tempfile.mkstemp(suffix=".wav", prefix=CLIP_PREFIX)
    os.close(fd)
    return Path(name)


def record_fixed(card: int, dev: int, path: Path, seconds: float) -> bool:
    """Record a fixed window (used by wake-word listening).
    Returns False if nothing usable was captured."""
    # arecord -d takes whole seconds only; 2.5 makes it exit immediately.
    result = subprocess.run(
        ["arecord", "-D", f"plughw:{card},{dev}", "-f", "S16_LE",
         "-r", "16000", "-c", "1", "-d", str(max(1, int(round(seconds)))),
         str(path)],
        stderr=subprocess.PIPE, text=True, preexec_fn=_die_with_parent,
    )
    if result.returncode != 0:
        print(f"arecord failed: {result.stderr.strip()}", flush=True)
    return path.exists() and path.stat().st_size > 1000


def _stream_with_timeout(llm, text, on_metrics_update, model, categories,
                         on_chunk=None, chunk_timeout=90.0,
                         total_timeout=240.0) -> tuple[list[str], bool]:
    """Stream the answer, but never block forever.

    The demo showed the kami "thinking" for 5+ minutes with no traceback: a
    request can be silently QUEUED behind another one (e.g. while Ollama is
    loading a newly selected model), and a plain `for chunk in ...` waits
    indefinitely. Here the generator runs in a worker thread and chunks are
    read with timeouts — if none arrive for `chunk_timeout` (or the whole
    turn exceeds `total_timeout`), we give up instead of hanging the loop.
    Returns (chunks, timed_out).
    """
    q: queue.Queue = queue.Queue()

    def produce():
        try:
            for chunk in llm.stream_response(
                text, on_metrics_update=on_metrics_update,
                model=model, memory_categories=categories,
            ):
                q.put(("chunk", chunk))
        except Exception as exc:            # noqa: BLE001 — worker boundary
            q.put(("error", str(exc)))
        finally:
            q.put(("done", None))

    threading.Thread(target=produce, daemon=True).start()
    start = time.time()
    parts: list[str] = []
    while True:
        if time.time() - start > total_timeout:
            return parts, True
        try:
            kind, payload = q.get(timeout=chunk_timeout)
        except queue.Empty:
            return parts, True
        if kind == "done":
            return parts, False
        if kind == "error":
            print(f"(model error: {payload})", flush=True)
            return parts, True
        parts.append(payload)
        if on_chunk:
            on_chunk("".join(parts))


def answer(llm: LLMManager, status: StatusStore, text: str, stt_s: float,
           on_chunk=None) -> bool:
    """One full turn: publish, stream the kami's answer, record metrics.

    on_chunk(partial_answer) is called after each streamed token, so the
    caller can push the answer live to the web page instead of waiting for
    the whole turn to finish. Returns True if the model answered; False if
    it stalled or errored (the turn is still logged, marked not ok).
    """
    print(f'🙂 YOU (heard, {stt_s:.1f}s): {text}')
    status.submit_question(text)   # question visible on the web right away

    print("🐢 KAMI is thinking...", end="", flush=True)
    # Same shared settings the web page edits — one node, one behaviour.
    categories = LLMManager.resolve_categories(text, node_settings.memory_categories())
    parts, timed_out = _stream_with_timeout(
        llm, text,
        on_metrics_update=status.update_model,
        model=node_settings.model(),
        categories=categories,
        on_chunk=on_chunk,
    )
    if timed_out:
        print("\r🐢 KAMI: (no answer — the model stalled)", flush=True)
    elif parts:
        print("\r🐢 KAMI: " + "".join(parts), flush=True)
    else:
        print("\r🐢 KAMI: (silence)", flush=True)
    status.complete_turn("".join(parts), success=bool(parts) and not timed_out)
    m = llm.metrics
    print(f"[stt {stt_s:.1f}s | ttft {m.ttft_seconds:.1f}s | "
          f"{m.tokens_per_second:.1f} tok/s]\n")
    return bool(parts) and not timed_out


def push_to_talk_loop(llm, status, card, dev) -> None:
    while True:
        input(">>> press Enter, speak, then just pause ")
        clip = new_clip()
        print("🎤 listening...", end="", flush=True)
        ok = record_until_silence(card, dev, clip)
        print("\r🎤 (transcribing...)      ", end="", flush=True)
        if not ok:
            print("\r(no speech heard — try again)      \n")
            clip.unlink(missing_ok=True)
            continue
        t0 = time.perf_counter()
        text = transcribe(clip)
        stt_s = time.perf_counter() - t0
        clip.unlink(missing_ok=True)
        print("\r" + " " * 30 + "\r", end="")
        if not text:
            print("(heard nothing intelligible — try again)\n")
            continue
        answer(llm, status, text, stt_s)


def wake_word_loop(llm, status, card, dev, wake_word: str) -> None:
    """Listen in short windows; the wake word arms the kami.

    Saying e.g. "kami, how warm is the water?" answers immediately; saying
    just "kami" makes it listen for the next utterance (up to ~8 s).
    Cheap-and-cheerful VAD: whisper on 2.5 s windows; silence = empty
    transcript.
    """
    print(f'(wake word: "{wake_word}" — say it to get the kami\'s attention)')
    armed = False
    while not STOP.is_set():
        clip = new_clip()
        # Short window to catch the wake word; once armed, listen for a full
        # sentence and stop on the speaker's pause.
        ok = (record_until_silence(card, dev, clip, max_seconds=12.0)
              if armed else record_fixed(card, dev, clip, 3))
        if not ok:
            clip.unlink(missing_ok=True)
            if armed:
                print("(still listening... say something)")
            continue
        t0 = time.perf_counter()
        text = transcribe(clip).lower()
        stt_s = time.perf_counter() - t0
        clip.unlink(missing_ok=True)

        if not text:
            if armed:
                print("(still listening... say something)")
            continue

        wake = wake_word.lower()
        if wake in text:
            # Everything after the wake word is the question, if any.
            after = text.split(wake, 1)[1].strip(" ,.?!")
            if after:
                answer(llm, status, after, stt_s)
                armed = False
            else:
                armed = True
                print("KAMI: yes? (listening...)\n")
        elif armed:
            answer(llm, status, text, stt_s)
            armed = False
        else:
            print(f'(ignored: "{text}")')


def serve_loop(llm, status, card, dev) -> None:
    """Microphone as a service: the web page decides what it does.

    Streams the mic through Silero VAD. Whisper is only called on actual
    speech, so silence cannot produce hallucinated turns. Two things can wake
    the kami: the wake word (if enabled) or a "Speak" press on the page.
    Publishes a heartbeat so the page can tell "idle" from "not running".
    """
    print("(serving — Silero VAD + whisper; control from the web page)")
    armed = False                      # wake word heard, awaiting the question
    last_request = node_settings.load()["listen_request"]

    # A heartbeat is re-published every 2 s no matter what the loop is doing,
    # so a long "thinking" or a silent wake-wait can't make the page believe
    # the process died (it used to read "voice process not working"). It also
    # carries the live question + partial answer so the web page can stream
    # the kami's reply instead of showing it all at once at the end.
    cur = {"state": "starting", "detail": "", "heard": "", "vad": 0.0,
           "question": "", "partial": ""}

    def beat():
        while not STOP.is_set():
            node_settings.publish_voice_state(
                cur["state"], cur["detail"], cur["heard"] or None, cur["vad"],
                cur["question"], cur["partial"])
            time.sleep(2)
    threading.Thread(target=beat, daemon=True).start()

    def publish(state, detail="", heard=None):
        cur["state"], cur["detail"] = state, detail
        if heard is not None:
            cur["heard"] = heard
        node_settings.publish_voice_state(
            state, detail, heard, cur["vad"], cur["question"], cur["partial"])

    def say_nothing_heard():
        """A visible "I didn't hear that" so a dead capture is never silent."""
        publish("empty", "I didn't hear that — try again")
        time.sleep(1.6)   # hold it long enough to be seen before the next state

    while not STOP.is_set():
        settings = node_settings.load()
        voice = settings["voice"]
        wake_on = voice["enabled"]
        requested = settings["listen_request"] > last_request

        # A press on the page always wins, whether or not the wake word is on.
        if requested:
            last_request = settings["listen_request"]
            armed = True

        # Nothing to do: wake word off and nobody pressed speak.
        if not armed and not wake_on:
            publish("off")
            time.sleep(0.4)
            continue

        # Either armed (record the question) or listening for the wake word.
        publish(
            "listening" if armed else "waking",
            "speak now" if armed else f'say "{voice["wake_word"]}"')

        # Capture one utterance. It stops on its own after ~1 s of silence
        # (VAD), at the max-duration cap, or if the page presses Stop/Speak.
        stop_at = settings["stop_request"]
        req_at = settings["listen_request"]
        pcm = capture_utterance(
            card, dev,
            max_seconds=20.0,
            min_silence_ms=1000,
            auto_stop=True,
            vad_enabled=voice.get("vad", True),
            die_with_parent=_die_with_parent,
            on_vad=lambda p: cur.__setitem__("vad", round(p, 3)),
            check=lambda: (node_settings.load()["stop_request"] > stop_at
                           or node_settings.load()["listen_request"] > req_at),
        )
        cur["vad"] = 0.0
        if pcm is None:
            if armed:
                say_nothing_heard()
            armed = False
            continue

        # "transcribing" for a real question; a wake-word look gets its own
        # quieter "checking" state so the page never confuses the two.
        publish("transcribing" if armed else "checking")
        t0 = time.perf_counter()
        text = transcribe_pcm(pcm).strip()
        stt_s = time.perf_counter() - t0
        if not text:
            if armed:
                say_nothing_heard()
            armed = False
            continue

        if armed:
            question = text
        else:
            hit, rest = split_on_wake(text, voice["wake_word"])
            if not hit:
                # Not the wake word: go back to waiting. Nothing flashy on the
                # page — a blinking button here would read as "accepted".
                print(f'(ignored: "{text}")', flush=True)
                continue
            rest = rest.strip(" ,.?!")
            wake = voice["wake_word"].lower()
            # "turtle" alone, or just its echo ("turtle turtle"): arm listening
            # for the real question rather than answering the echo.
            if not rest or (len(rest.split()) == 1 and
                            difflib.SequenceMatcher(None, rest.lower(), wake).ratio() >= WAKE_SIMILARITY):
                armed = True
                print("KAMI: yes? (listening...)", flush=True)
                continue
            question = rest

        # Stream the answer to the web page as it is generated.
        cur["question"] = question
        cur["partial"] = ""
        publish("thinking", question[:60], heard=text)

        def on_chunk(partial):
            cur["partial"] = partial
            node_settings.publish_voice_state(
                cur["state"], cur["detail"], cur["heard"] or None, cur["vad"],
                cur["question"], partial)

        answer_ok = answer(llm, status, question, stt_s, on_chunk=on_chunk)
        if not answer_ok:
            publish("error", "the model didn't answer — try again")
            time.sleep(1.6)
        cur["question"] = ""
        cur["partial"] = ""
        armed = False


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--wake", metavar="WORD", default=None,
                    help='wake-word mode (e.g. --wake wake)')
    ap.add_argument("--serve", action="store_true",
                    help="obey the web page's voice settings (recommended)")
    ap.add_argument("--no-takeover", action="store_true",
                    help="refuse to start if another voice loop is running")
    args = ap.parse_args()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    claim_microphone(takeover=not args.no_takeover)

    found = find_capture_card()
    if not found:
        sys.exit("No ReSpeaker found. Check `arecord -l`.")
    card, dev = found

    llm = LLMManager()
    status = StatusStore(source="voice")
    llm.check_availability()
    status.update_model(llm.metrics)

    print(f"SolarTurtle voice loop (mic: card {card}, model: {config.MODEL_NAME})")

    try:
        if args.serve:
            serve_loop(llm, status, card, dev)
        elif args.wake:
            wake_word_loop(llm, status, card, dev, args.wake)
        else:
            print("Enter to talk, Enter to stop, Ctrl+C to quit.\n")
            push_to_talk_loop(llm, status, card, dev)
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        node_settings.publish_voice_state("offline")
        status.clear()
        print("voice loop stopped.")


if __name__ == "__main__":
    main()
