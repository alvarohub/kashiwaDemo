from __future__ import annotations # enables postponed evaluation of type annotations (PEP 563)

import json
import os
import queue
import sys
import threading
import time
from typing import Iterator

import requests
from flask import Flask, Response, jsonify, request, send_file

try:
    import waitress
    _HAVE_WAITRESS = True
except ImportError:
    waitress = None
    _HAVE_WAITRESS = False

import config
import node_settings
import reports_reader
from hardware_monitor import HardwareMonitor
from llm_manager import LLMManager
from status_store import StatusStore


class WebApplication:
    """Serves the chat page and streams model answers to browsers.

    Shares LLMManager / HardwareMonitor / StatusStore with the other
    frontends, so control.py and console.py keep working alongside.
    Generation is single-slot (Ollama on a Pi is effectively serial); a
    second client gets a "busy" event instead of a parallel generation.
    """

    def __init__(self):
        self.stop_event = threading.Event()
        self.hardware_monitor = HardwareMonitor()
        self.llm_manager = LLMManager()
        self.status_store = StatusStore()
        self.background_thread: threading.Thread | None = None

        # Single generation slot (Ollama on a Pi is effectively serial).
        self._generation_lock = threading.Lock()

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def run(
        self,
        host: str = "0.0.0.0",
        port: int = 8080,
        ssl_context: tuple[str, str] | None = None,
    ) -> None:
        self.llm_manager.check_availability()
        self.status_store.update_model(self.llm_manager.metrics)
        self._start_background_thread()
        app = self._build_app()
        scheme = "https" if ssl_context else "http"
        try:
            if _HAVE_WAITRESS:
                # Production WSGI server: robust against dropped/sleeping
                # clients, which is what killed the Flask dev server.
                print(f"Serving on {scheme}://{host}:{port} (waitress)")
                kwargs: dict = {"host": host, "port": port, "threads": 8}
                if ssl_context:
                    # waitress has no TLS; terminate via its url_scheme only.
                    # For real TLS, use the Flask path below instead.
                    print("WARNING: waitress does not do TLS; using Flask server for HTTPS.")
                    app.run(host=host, port=port, threaded=True, ssl_context=ssl_context)
                else:
                    waitress.serve(app, **kwargs)
            else:
                print("waitress not installed; falling back to Flask dev server")
                print("(sudo apt install python3-waitress for a stable server)")
                app.run(host=host, port=port, threaded=True, ssl_context=ssl_context)
        finally:
            self._shutdown()

    def _start_background_thread(self) -> None:
        self.background_thread = threading.Thread(
            target=self._background_loop,
            name="solarturtle-monitor-loop",
            daemon=True,
        )
        self.background_thread.start()

    def _background_loop(self) -> None:
        while not self.stop_event.is_set():
            hardware = self.hardware_monitor.read_snapshot()
            self.status_store.update_hardware(hardware)
            self.stop_event.wait(config.HARDWARE_SAMPLE_INTERVAL)

    def _shutdown(self) -> None:
        self.stop_event.set()
        if self.background_thread and self.background_thread.is_alive():
            self.background_thread.join(timeout=1.0)
        self.status_store.clear()

    # ------------------------------------------------------------------
    # generation worker (single slot)
    # ------------------------------------------------------------------

    def _stream_question(
        self,
        question: str,
        model: str | None = None,
        memory_categories: set[str] | None = None,
    ) -> Iterator[dict]:
        """Yield SSE event dicts for one question.

        The caller holds a per-request response stream, so the client that
        asked is the client that receives the answer (no shared broadcast).
        """
        self.status_store.submit_question(question)

        # Single-slot: a second concurrent asker is told to wait rather than
        # starting a parallel generation the Pi can't sustain.
        if not self._generation_lock.acquire(blocking=False):
            yield {"event": "busy", "data": "Answering another question…"}
            return

        try:
            if not self.llm_manager.metrics.available:
                self.llm_manager.check_availability()
                self.status_store.update_model(self.llm_manager.metrics)
            if not self.llm_manager.metrics.available:
                self.status_store.complete_turn(
                    answer=None, success=False, error="model_unavailable",
                )
                yield {"event": "error", "data": "Model unavailable"}
                return

            yield {"event": "start", "data": ""}
            response_parts: list[str] = []
            try:
                for chunk in self.llm_manager.stream_response(
                    question,
                    on_metrics_update=self.status_store.update_model,
                    model=model,
                    memory_categories=memory_categories,
                ):
                    response_parts.append(chunk)
                    yield {"event": "chunk", "data": chunk}

                answer = "".join(response_parts)
                self.status_store.complete_turn(answer=answer, success=True)
                yield {
                    "event": "done",
                    "data": json.dumps({
                        "answer": answer,
                        "metrics": {
                            "ttft_seconds": self.llm_manager.metrics.ttft_seconds,
                            "total_response_seconds": self.llm_manager.metrics.total_response_seconds,
                            "output_tokens": self.llm_manager.metrics.output_tokens,
                            "tokens_per_second": self.llm_manager.metrics.tokens_per_second,
                        },
                    }),
                }
            except requests.RequestException:
                self.status_store.update_model(self.llm_manager.metrics)
                self.status_store.complete_turn(
                    answer="".join(response_parts) or None,
                    success=False,
                    error="communication_error",
                )
                yield {"event": "error", "data": "Model error"}
        finally:
            self._generation_lock.release()

    # ------------------------------------------------------------------
    # Flask app
    # ------------------------------------------------------------------

    def _build_app(self) -> Flask:
        app = Flask(__name__)

        @app.get("/")
        def index():
            return send_file("static/chat.html")

        # --- Captive-portal probe endpoints ---------------------------------
        # When the AP runs with wildcard DNS (portal_up.sh), phones probe
        # these URLs to detect a portal. Answering with the chat page (or a
        # non-204 redirect) makes the OS pop the chat open automatically.
        @app.get("/generate_204")          # Android
        @app.get("/gen_204")               # Android alt
        def android_probe():
            from flask import redirect
            return redirect("/", code=302)

        @app.get("/hotspot-detect.html")   # iOS / macOS
        @app.get("/library/test/success.html")  # iOS alt
        def apple_probe():
            return send_file("static/chat.html")

        @app.get("/ncsi.txt")              # Windows
        @app.get("/connecttest.txt")       # Windows alt
        def windows_probe():
            from flask import redirect
            return redirect("/", code=302)

        @app.get("/dashboard")
        def dashboard():
            return send_file("static/dashboard.html")

        @app.get("/manifest.json")
        def manifest():
            """Web-app manifest so 'Add to Home Screen' opens fullscreen (no
            browser address bar) on the phone."""
            return jsonify({
                "name": "SolarTurtle",
                "short_name": "Kami",
                "display": "standalone",
                "background_color": "#000000",
                "theme_color": "#000000",
                "start_url": "/",
            })

        # Memory files the dashboard can read (relative to the repo root).
        _MEM_DIRS = ("identity", "sensors", "community")

        @app.after_request
        def no_api_cache(resp):
            # Phones cache plain GETs happily: without this, a memory file
            # edited on the Mac and pushed still reads as the old version.
            if request.path.startswith("/api/"):
                resp.headers["Cache-Control"] = "no-store, must-revalidate"
            return resp

        @app.get("/api/files")
        def list_files():
            base = os.path.dirname(os.path.abspath(__file__))
            out = []
            for d in _MEM_DIRS:
                dirpath = os.path.join(base, d)
                if os.path.isdir(dirpath):
                    for name in sorted(os.listdir(dirpath)):
                        if name.endswith(".md"):
                            out.append(f"{d}/{name}")
            return jsonify({"files": out})

        @app.get("/api/file")
        def read_file_ep():
            name = request.args.get("name", "")
            # Path-traversal guard: must be "<memdir>/<file>.md", no "..".
            parts = name.split("/")
            if (len(parts) != 2 or parts[0] not in _MEM_DIRS
                    or ".." in name or not parts[1].endswith(".md")):
                return "not allowed", 403
            base = os.path.dirname(os.path.abspath(__file__))
            path = os.path.join(base, parts[0], parts[1])
            try:
                with open(path, encoding="utf-8") as f:
                    return f.read(), 200, {"Content-Type": "text/plain; charset=utf-8"}
            except OSError:
                return "not found", 404

        @app.get("/api/models")
        def models():
            try:
                data = requests.get(
                    f"{self.llm_manager.api_base}/tags",
                    timeout=config.REQUEST_TIMEOUT_SECONDS,
                ).json()
                names = [m.get("name") or m.get("model") for m in data.get("models", [])]
            except (requests.RequestException, ValueError):
                names = []
            return jsonify({"models": names, "default": config.MODEL_NAME})

        @app.post("/api/note")
        def note():
            """Append a community note to community/notes.md."""
            import os
            from datetime import datetime
            data = request.get_json(force=True, silent=True) or {}
            text = (data.get("note") or "").strip()
            if not text:
                return jsonify({"error": "empty note"}), 400
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "community", "notes.md")
            line = f"{datetime.now():%Y-%m-%d %H:%M}  {text}\n"
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
            return jsonify({"ok": True})

        @app.post("/api/reset-logs")
        def reset_logs():
            """Clear the node's accumulated logs: the conversation history and
            the daily journal. Memory notes and sensor telemetry are kept."""
            base = os.path.dirname(os.path.abspath(__file__))
            cleared = []
            for rel in ("identity/turn_log.jsonl", "identity/journal.md"):
                path = os.path.join(base, rel)
                try:
                    with open(path, "w", encoding="utf-8") as f:
                        f.write("")
                    cleared.append(rel)
                except OSError:
                    pass
            return jsonify({"cleared": cleared})

        @app.get("/api/status")
        def status():
            snapshot = self.hardware_monitor.read_snapshot()
            metrics = self.llm_manager.metrics
            # Turns that happened elsewhere (the microphone, the console) are
            # read from the append-only turn log, NOT from the status file:
            # every frontend rewrites that file wholesale and would erase
            # another process's turn before the page ever polled it.
            last_turn = None
            try:
                log_path = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)),
                    "identity", "turn_log.jsonl",
                )
                with open(log_path, encoding="utf-8") as f:
                    lines = f.readlines()[-1:]
                if lines:
                    last_turn = json.loads(lines[0])
            except (OSError, ValueError):
                pass
            return jsonify({
                "model": metrics.model_name,
                "available": metrics.available,
                "active": metrics.active,
                "cpu_percent": snapshot.cpu_percent,
                "ram_percent": snapshot.ram_percent,
                "temperature_c": snapshot.temperature_c,
                "last_turn": last_turn,
                "settings": node_settings.load(),
                "voice": node_settings.voice_state(),
            })

        @app.post("/api/settings")
        def set_settings():
            """The page edits the NODE's settings, not its own: what is saved
            here is what the microphone and the console will use too."""
            data = request.get_json(force=True, silent=True) or {}
            patch: dict[str, object] = {}
            if isinstance(data.get("memory"), list):
                patch["memory"] = [c for c in data["memory"]
                                   if c in reports_reader.CATEGORIES]
            if isinstance(data.get("model"), str) and data["model"]:
                patch["model"] = data["model"]
            return jsonify(node_settings.save(**patch))

        @app.post("/api/voice")
        def set_voice():
            """Turn the wake word on/off, change the word, toggle VAD.
            The voice process polls these settings; nothing is started here."""
            data = request.get_json(force=True, silent=True) or {}
            patch: dict[str, object] = {}
            if "enabled" in data:
                patch["enabled"] = bool(data["enabled"])
            if "vad" in data:
                patch["vad"] = bool(data["vad"])
            word = (data.get("wake_word") or "").strip()
            if word:
                patch["wake_word"] = word
            return jsonify(node_settings.save(voice=patch))

        @app.post("/api/listen")
        def listen_now():
            """Arm exactly one capture (the page's press-to-talk button)."""
            return jsonify(node_settings.save(listen_request=time.time()))

        @app.post("/api/stop")
        def stop_now():
            """End the current capture early (the page's Stop button)."""
            return jsonify(node_settings.save(stop_request=time.time()))

        @app.post("/api/voice/restart")
        def voice_restart():
            """Last resort: restart the voice process from the page.
            Works because set_autostart.sh scoped a passwordless sudo
            rule for exactly this command."""
            import subprocess
            try:
                result = subprocess.run(
                    ["sudo", "-n", "systemctl", "restart", "solarturtle-voice"],
                    capture_output=True, text=True, timeout=15,
                )
                return jsonify({"ok": result.returncode == 0,
                                "err": (result.stderr or "").strip()[:200]})
            except Exception as exc:                 # noqa: BLE001
                return jsonify({"ok": False, "err": str(exc)}), 500

        @app.post("/api/ask")
        def ask():
            data = request.get_json(force=True, silent=True) or {}
            question = (data.get("question") or "").strip()
            model = (data.get("model") or "").strip() or None
            # The page is the node's control panel: whatever it sends becomes
            # the SHARED setting, so voice and console obey it too.
            toggles = data.get("memory")
            if isinstance(toggles, list) or model:
                patch = {}
                if isinstance(toggles, list):
                    patch["memory"] = [c for c in toggles
                                       if c in reports_reader.CATEGORIES]
                if model:
                    patch["model"] = model
                node_settings.save(**patch)
            model = model or node_settings.model()
            memory_categories = self.llm_manager.resolve_categories(
                question, node_settings.memory_categories(),
            )
            if not question:
                return jsonify({"error": "empty question"}), 400

            def generate():
                try:
                    for event in self._stream_question(
                        question, model=model, memory_categories=memory_categories,
                    ):
                        yield f"event: {event['event']}\ndata: {event['data']}\n\n"
                except Exception:
                    # Never let a generation error kill the HTTP response.
                    import traceback
                    traceback.print_exc()
                    yield "event: error\ndata: internal error\n\n"

            return Response(
                generate(),
                mimetype="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                },
            )

        return app


def main() -> None:
    # --https: serve over TLS with the self-signed cert from make_cert.sh,
    # so the in-page microphone (Web Speech API) works in browsers.
    if "--https" in sys.argv:
        base = os.path.dirname(os.path.abspath(__file__))
        ctx = (
            os.path.join(base, "certs", "selfsigned.pem"),
            os.path.join(base, "certs", "selfsigned.key"),
        )
        if not (os.path.exists(ctx[0]) and os.path.exists(ctx[1])):
            sys.exit("No certs found. Run: bash make_cert.sh")
        WebApplication().run(port=8443, ssl_context=ctx)
    else:
        WebApplication().run()


if __name__ == "__main__":
    main()
