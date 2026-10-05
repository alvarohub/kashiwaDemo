from __future__ import annotations

import signal
import threading

try:
    import tkinter as tk
except ImportError:
    tk = None  # python3-tk not installed; console mode only.

import requests

import config
import node_settings
from hardware_monitor import HardwareMonitor
from llm_manager import LLMManager, ModelMetrics
from status_store import StatusStore

if tk is not None:
    from lcd_gui import ConversationGUI
else:
    ConversationGUI = None


class SolarTurtleApplication:
    """Coordinates the GUI, local LLM, hardware monitor and SSH status data."""

    def __init__(self):
        self.stop_event = threading.Event()
        self.hardware_monitor = HardwareMonitor()
        self.llm_manager = LLMManager()
        self.status_store = StatusStore()

        self.background_thread: threading.Thread | None = None
        self.generation_thread: threading.Thread | None = None
        self.generation_active = False

        self.root: tk.Tk | None = None
        self.gui: ConversationGUI | None = None

    def run(self) -> None:
        self._install_signal_handlers()

        self.llm_manager.check_availability()
        self._update_model_status(self.llm_manager.metrics)
        self._start_background_thread()

        if tk is None:
            print("Tkinter is not installed; starting console mode.")
            self._run_console_fallback()
            return

        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            # No display available (SSH/headless boot): fall back to console
            # mode instead of crashing. The LLM and monitoring services are
            # shared, so behaviour is identical apart from the frontend.
            print(f"No display available ({exc}); starting console mode.")
            self._run_console_fallback()
            return

        self.gui = ConversationGUI(
            root=self.root,
            on_submit=self._handle_prompt,
            on_exit=self._request_exit,
            on_generation_finished=self._generation_finished,
        )

        try:
            self.root.mainloop()
        finally:
            self._shutdown()

    def _run_console_fallback(self) -> None:
        # Import lazily so the GUI path never pays for it, and hand over the
        # already-initialised shared services.
        from console import ConsoleApplication

        # Restore the default Ctrl+C behaviour (KeyboardInterrupt) so the
        # console input loop can be interrupted; the GUI handler above expects
        # a Tk root that does not exist on this path.
        signal.signal(signal.SIGINT, signal.default_int_handler)

        console_app = ConsoleApplication()
        console_app.llm_manager = self.llm_manager
        console_app.status_store = self.status_store
        console_app.hardware_monitor = self.hardware_monitor
        console_app.stop_event = self.stop_event
        console_app.background_thread = self.background_thread
        self.background_thread = None  # ownership moves to the console app
        console_app.run()

    def _handle_prompt(self, prompt: str) -> bool:
        if self.stop_event.is_set() or self.gui is None:
            return False

        # Do not start a second generation while the current response is active.
        # The user may continue typing; Enter is accepted after the GUI has
        # finished displaying the previous response and its spacing.
        if self.generation_active:
            return False

        self.status_store.submit_question(prompt)
        self.gui.add_user_message(prompt)
        self.gui.start_model_message()

        if not self.llm_manager.metrics.available:
            self.gui.show_model_error("Model unavailable")
            self.status_store.complete_turn(
                answer=None,
                success=False,
                error="model_unavailable",
            )
            return True

        self._start_generation(prompt)
        return True

    def _start_generation(self, prompt: str) -> None:
        self.generation_active = True
        self.generation_thread = threading.Thread(
            target=self._generation_loop,
            args=(prompt,),
            name="solarturtle-generation-loop",
            daemon=True,
        )
        self.generation_thread.start()

    def _generation_loop(self, prompt: str) -> None:
        assert self.gui is not None
        generation_completed = False
        error_reported = False
        response_parts: list[str] = []

        try:
            for chunk in self.llm_manager.stream_response(
                prompt,
                on_metrics_update=self._update_model_status,
                model=node_settings.model(),
                memory_categories=LLMManager.resolve_categories(
                    prompt, node_settings.memory_categories(),
                ),
            ):
                if self.stop_event.is_set():
                    return
                response_parts.append(chunk)
                self.gui.queue_model_chunk(chunk)

            if not self.stop_event.is_set():
                self.gui.queue_model_complete()
                generation_completed = True
                self.status_store.complete_turn(
                    answer="".join(response_parts),
                    success=True,
                )

        except requests.RequestException:
            if not self.stop_event.is_set():
                self.gui.queue_model_error("Model error")
                error_reported = True
            self._update_model_status(self.llm_manager.metrics)
            if not self.stop_event.is_set():
                self.status_store.complete_turn(
                    answer="".join(response_parts) or None,
                    success=False,
                    error="communication_error",
                )
        finally:
            if not generation_completed and not error_reported and not self.stop_event.is_set():
                self.gui.queue_model_error("Model error")
                self.status_store.complete_turn(
                    answer="".join(response_parts) or None,
                    success=False,
                    error="generation_error",
                )
            self.generation_active = False

    def _generation_finished(self) -> None:
        # Called by the GUI only after the final model event and spacing have
        # been rendered, preventing prompt ordering races.
        self.generation_active = False

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

    def _update_model_status(self, metrics: ModelMetrics) -> None:
        if not self.stop_event.is_set():
            self.status_store.update_model(metrics)

    def _request_exit(self) -> None:
        self.stop_event.set()
        if self.root is not None and self.root.winfo_exists():
            self.root.quit()

    def _install_signal_handlers(self) -> None:
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

    def _signal_handler(self, _signum, _frame) -> None:
        self.stop_event.set()
        if self.root is not None:
            try:
                self.root.after(0, self.root.quit)
            except tk.TclError:
                pass

    def _shutdown(self) -> None:
        self.stop_event.set()

        if self.background_thread and self.background_thread.is_alive():
            self.background_thread.join(timeout=1.0)

        if self.generation_thread and self.generation_thread.is_alive():
            self.generation_thread.join(timeout=config.GENERATION_SHUTDOWN_TIMEOUT)

        self.status_store.clear()

        if self.root is not None:
            try:
                self.root.destroy()
            except tk.TclError:
                pass


def main() -> None:
    SolarTurtleApplication().run()


if __name__ == "__main__":
    main()
