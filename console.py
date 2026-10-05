from __future__ import annotations

import threading

import requests

import config
import node_settings
from hardware_monitor import HardwareMonitor
from llm_manager import LLMManager
from status_store import StatusStore


class ConsoleApplication:
    """Screenless frontend: plain stdin/stdout chat reusing the shared core.

    Keeps the status store and hardware monitor running so control.py works
    alongside it, exactly like the Tkinter application does.
    """

    def __init__(self):
        self.stop_event = threading.Event()
        self.hardware_monitor = HardwareMonitor()
        self.llm_manager = LLMManager()
        self.status_store = StatusStore(source="console")
        self.background_thread: threading.Thread | None = None

    def run(self) -> None:
        available, reason = self.llm_manager.check_availability()
        self.status_store.update_model(self.llm_manager.metrics)
        self._start_background_thread()

        print(f"{config.PROGRAM_NAME} {config.PROGRAM_VERSION} (console mode)")
        if available:
            print(f"Model {self.llm_manager.model_name} is ready.")
        else:
            print(f"WARNING: {reason}")
            print("You can still type, but answers will fail until this is fixed.")
        print("Type a question and press Enter. Ctrl+C or Ctrl+D to exit.")
        print()

        try:
            self._prompt_loop()
        finally:
            self._shutdown()

    def _prompt_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                prompt = input(config.USER_PREFIX).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if not prompt:
                continue

            self.status_store.submit_question(prompt)

            if not self.llm_manager.metrics.available:
                available, _reason = self.llm_manager.check_availability()
                self.status_store.update_model(self.llm_manager.metrics)
                if not available:
                    print(f"{config.MODEL_PREFIX}Model unavailable")
                    self.status_store.complete_turn(
                        answer=None,
                        success=False,
                        error="model_unavailable",
                    )
                    print()
                    continue

            print(config.MODEL_PREFIX, end="", flush=True)
            self._stream_answer(prompt)
            self._print_metrics()
            print()

    def _stream_answer(self, prompt: str) -> None:
        response_parts: list[str] = []
        try:
            for chunk in self.llm_manager.stream_response(
                prompt,
                on_metrics_update=self.status_store.update_model,
                model=node_settings.model(),
                memory_categories=LLMManager.resolve_categories(
                    prompt, node_settings.memory_categories(),
                ),
            ):
                response_parts.append(chunk)
                print(chunk, end="", flush=True)

            print()
            self.status_store.complete_turn(
                answer="".join(response_parts),
                success=True,
            )
        except requests.RequestException:
            print("\nModel error")
            self.status_store.update_model(self.llm_manager.metrics)
            self.status_store.complete_turn(
                answer="".join(response_parts) or None,
                success=False,
                error="communication_error",
            )
        except KeyboardInterrupt:
            # Abort this answer only; return to the prompt instead of exiting.
            print("\n(interrupted)")
            self.status_store.complete_turn(
                answer="".join(response_parts) or None,
                success=False,
                error="interrupted",
            )

    def _print_metrics(self) -> None:
        metrics = self.llm_manager.metrics
        if metrics.total_response_seconds is None:
            return
        ttft = "--" if metrics.ttft_seconds is None else f"{metrics.ttft_seconds:.2f}s"
        rate = "--" if metrics.tokens_per_second is None else f"{metrics.tokens_per_second:.2f}"
        print(
            f"[ttft {ttft} | total {metrics.total_response_seconds:.2f}s"
            f" | {metrics.output_tokens or '--'} tokens | {rate} tok/s]"
        )

    def _start_background_thread(self) -> None:
        # May already be running when main.py fell back to console mode after
        # starting its shared services.
        if self.background_thread is not None and self.background_thread.is_alive():
            return
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


def main() -> None:
    ConsoleApplication().run()


if __name__ == "__main__":
    main()
