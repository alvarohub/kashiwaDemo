"""The node's live bulletin board.

Every frontend (web_server, console, lcd GUI) runs in its own process and
publishes "what is happening right now" to ONE shared JSON file
(config.STATUS_FILE, /tmp/solarturtle_status.json). control.py — the SSH
dashboard — is a separate process that polls that file and renders it.

Why a file and not, say, a socket or shared memory: it is the simplest
inter-process channel on a single machine, needs no broker, and the reader
side needs zero locks (see _write_locked for the atomic-write trick).

IMPORTANT — this is a LIVE PULSE, not a log:
  - It lives in /tmp, is deleted on clean shutdown (clear()), and control.py
    treats a file older than STATUS_STALE_AFTER_SECONDS as "process gone".
  - The node's PERSISTENT memory (the kami's soul) lives elsewhere:
    identity/ (identity, journal), community/ (notes), sensors/.
    Nothing here survives a reboot, and nothing here should.
  - Phase 4 idea (see PLAN.md): a logger could periodically fold these live
    stats (traffic, temperature) into a persistent self-report — this file
    is the natural source for it.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock

import config
from hardware_monitor import HardwareSnapshot
from llm_manager import ModelMetrics


class StatusStore:
    """Thread-safe publisher of the node's live status to the shared file.

    Writers (any frontend) call update_hardware / update_model /
    submit_question / complete_turn; the reader (control.py) just reads the
    file. All mutating methods take the same Lock because the web server
    writes from multiple waitress threads at once.

    Snapshot discipline: we store COPIES (HardwareSnapshot(**vars(...)),
    ModelMetrics(**vars(...))) so a caller that keeps mutating its own
    dataclass instance cannot silently corrupt the stored state.
    """

    def __init__(self, path: str = config.STATUS_FILE, source: str = "web"):
        self.path = Path(path)
        self.source = source   # which frontend we are: web / voice / console
        self._lock = Lock()
        self._hardware = HardwareSnapshot()
        self._model = ModelMetrics(model_name=config.MODEL_NAME)
        self._question_count = 0
        self._current_question: str | None = None
        self._completed_turn: dict | None = None

    def update_hardware(self, hardware: HardwareSnapshot) -> None:
        with self._lock:
            self._hardware = HardwareSnapshot(**vars(hardware))
            self._write_locked()

    def update_model(self, model: ModelMetrics) -> None:
        with self._lock:
            self._model = ModelMetrics(**vars(model))
            self._write_locked()

    def submit_question(self, question: str) -> None:
        with self._lock:
            self._question_count += 1
            self._current_question = question
            self._write_locked()

    def complete_turn(
        self,
        answer: str | None,
        success: bool,
        error: str | None = None,
    ) -> None:
        with self._lock:
            # The dashboard's scrollback: question + answer + the hardware and
            # model metrics AT THE MOMENT of completion (snapshotted now, so
            # later activity does not rewrite history).
            self._completed_turn = {
                "question": self._current_question,
                "answer": answer,
                "model": self._model_data_locked(),
                "hardware": self._hardware_data_locked(),
                "success": success,
                "error": error,
            }
            self._write_locked()
            self._journal_turn_locked()

    def _journal_turn_locked(self) -> None:
        """Append this turn as one JSON line to identity/turn_log.jsonl.

        Append-only is what makes this safe: several frontend processes write
        here concurrently, and unlike the whole-document status file they
        cannot overwrite each other. It is both the conversation history the
        journal writer folds into daily entries, and how the web page learns
        about turns that happened at the microphone.
        """
        turn = self._completed_turn
        if not turn:
            return
        answer = turn.get("answer") or ""
        record = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "source": self.source,
            "q": turn.get("question"),
            "a": answer[:2000],
            "ok": turn.get("success"),
            "tok_s": (turn.get("model") or {}).get("tokens_per_second"),
        }
        log_path = Path(__file__).parent / "identity" / "turn_log.jsonl"
        try:
            log_path.parent.mkdir(exist_ok=True)
            with log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except OSError:
            pass

    @property
    def question_count(self) -> int:
        """Number of questions submitted so far (used as a turn id by the
        web UI to dedupe voice turns)."""
        with self._lock:
            return self._question_count

    def latest_turn(self) -> dict | None:
        """A copy of the most recent completed turn (or None)."""
        with self._lock:
            return dict(self._completed_turn) if self._completed_turn else None

    def _hardware_data_locked(self) -> dict:
        return {
            "cpu_percent": self._hardware.cpu_percent,
            "ram_used_gb": self._hardware.ram_used_gb,
            "ram_total_gb": self._hardware.ram_total_gb,
            "ram_percent": self._hardware.ram_percent,
            "temperature_c": self._hardware.temperature_c,
        }

    def _model_data_locked(self) -> dict:
        return {
            "model_name": self._model.model_name,
            "ttft_seconds": self._model.ttft_seconds,
            "total_response_seconds": self._model.total_response_seconds,
            "prompt_tokens": self._model.prompt_tokens,
            "output_tokens": self._model.output_tokens,
            "tokens_per_second": self._model.tokens_per_second,
            "active": self._model.active,
            "available": self._model.available,
        }

    def _write_locked(self) -> None:
        data = {
            "hardware": self._hardware_data_locked(),
            "model": self._model_data_locked(),
            "question_count": self._question_count,
            "current_question": self._current_question,
            "generation": "GENERATING" if self._model.active else "IDLE",
            "completed_turn": self._completed_turn,
        }

        # ATOMIC WRITE: dump to a temp file in the same directory, then
        # os.replace() — a rename is atomic on POSIX, so control.py either
        # sees the previous complete JSON or the new complete JSON, never a
        # torn half-write. This is why the reader needs no lock at all.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=self.path.parent,
            delete=False,
        ) as temp_file:
            json.dump(data, temp_file)
            temp_name = temp_file.name

        os.replace(temp_name, self.path)

    def clear(self) -> None:
        with self._lock:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
