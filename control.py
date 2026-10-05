from __future__ import annotations

import json
import time
from pathlib import Path

import config


def format_percent(value) -> str:
    return "--" if value is None else f"{value:.0f}%"


def format_temperature(value) -> str:
    return "--" if value is None else f"{value:.1f}C"


def format_seconds(value) -> str:
    return "--" if value is None else f"{value:.2f}s"


def format_int(value) -> str:
    return "--" if value is None else str(value)


def format_rate(value) -> str:
    return "--" if value is None else f"{value:.1f}"


def format_ram(used, total, percent) -> str:
    if used is None or total is None or percent is None:
        return "--"
    return f"{used:.1f} / {total:.1f} GB ({percent:.0f}%)"


def read_status(path: Path) -> dict | None:
    try:
        if time.time() - path.stat().st_mtime > config.STATUS_STALE_AFTER_SECONDS:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def render(status: dict | None) -> None:
    print(f"{config.PROGRAM_NAME}  {config.PROGRAM_VERSION}")
    print("=" * 42)

    if status is None:
        print("Status: waiting for main SolarTurtle process...")
        return

    hardware = status.get("hardware", {})
    model = status.get("model", {})
    model_status = "RUNNING" if model.get("active") else (
        "READY" if model.get("available") else "OFFLINE"
    )

    print(f"{model.get('model_name', config.MODEL_NAME)} | {model_status}")
    print()
    print("HARDWARE")
    print(f"CPU:  {format_percent(hardware.get('cpu_percent'))}")
    print(
        f"RAM:  {format_ram(hardware.get('ram_used_gb'), hardware.get('ram_total_gb'), hardware.get('ram_percent'))}"
    )
    print(f"Temp: {format_temperature(hardware.get('temperature_c'))}")
    print()
    print("MODEL")
    print(f"Name:   {model.get('model_name', '--')}")
    print(f"TTFT:   {format_seconds(model.get('ttft_seconds'))}")
    print(f"Total:  {format_seconds(model.get('total_response_seconds'))}")
    print(f"Prompt: {format_int(model.get('prompt_tokens'))}")
    print(f"Output: {format_int(model.get('output_tokens'))}")
    print(f"Tok/s:  {format_rate(model.get('tokens_per_second'))}")
    print()
    print("Ctrl+C to close control view")


class Dashboard:
    """Keeps completed turns in scrollback and redraws only the live section."""

    def __init__(self):
        self.live_line_count = 0
        self.last_completed_turn: dict | None = None

    def print_model_configuration(self) -> None:
        thick = "=" * 42
        thin = "-" * 21
        print(thick)
        print("ACTIVE MODEL CONFIGURATION")
        print(thin)
        print(f"Model Name: {config.MODEL_NAME}")
        print(f"Ollama API: {config.OLLAMA_API_BASE}")
        print(f"Num Predict: {config.NUM_PREDICT}")
        print(f"Conversation History: {config.CONVERSATION_HISTORY}")
        print(f"Request Timeout: {config.REQUEST_TIMEOUT_SECONDS}s")
        print(f"Generation Shutdown Timeout: {config.GENERATION_SHUTDOWN_TIMEOUT}s")
        print()
        print("System Prompt:")
        print(config.SYSTEM_PROMPT)
        print(thick)

    def update(self, status: dict | None) -> None:
        if status is None:
            self._replace_live(["Status: waiting for main SolarTurtle process..."])
            return

        completed_turn = status.get("completed_turn")
        if completed_turn and completed_turn != self.last_completed_turn:
            self._remove_live()
            self._print_completed_turn(completed_turn, status.get("question_count", 1))
            self.last_completed_turn = completed_turn

        live_lines = self._live_lines(status)
        self._replace_live(live_lines)

    def _live_lines(self, status: dict) -> list[str]:
        hardware = status.get("hardware", {})
        return [
            "=" * 42,
            "LIVE HARDWARE",
            f"CPU:  {format_percent(hardware.get('cpu_percent'))}",
            f"RAM:  {format_ram(hardware.get('ram_used_gb'), hardware.get('ram_total_gb'), hardware.get('ram_percent'))}",
            f"Temp: {format_temperature(hardware.get('temperature_c'))}",
        ]

    def _print_completed_turn(self, turn: dict, question_number: int) -> None:
        thick = "=" * 42
        thin = "-" * 21
        hardware = turn.get("hardware", {})
        model = turn.get("model", {})

        print()
        print(thick)
        print(f"QUESTION {question_number}")
        print(thin)
        print()
        print("HARDWARE")
        print(f"CPU:  {format_percent(hardware.get('cpu_percent'))}")
        print(f"RAM:  {format_ram(hardware.get('ram_used_gb'), hardware.get('ram_total_gb'), hardware.get('ram_percent'))}")
        print(f"Temp: {format_temperature(hardware.get('temperature_c'))}")
        print(thin)
        print()
        print("MODEL")
        print(f"TTFT:   {format_seconds(model.get('ttft_seconds'))}")
        print(f"Total:  {format_seconds(model.get('total_response_seconds'))}")
        print(f"Prompt: {format_int(model.get('prompt_tokens'))}")
        print(f"Output: {format_int(model.get('output_tokens'))}")
        print(f"Tok/s:  {format_rate(model.get('tokens_per_second'))}")
        print(thin)
        print()
        print("Question:")
        print(turn.get("question", ""))
        print()
        print("Answer:")
        if turn.get("success"):
            print(turn.get("answer", ""))
        else:
            print("COMMUNICATION ERROR")
            if turn.get("answer"):
                print(turn["answer"])
        print()
        print(thick)

    def _replace_live(self, lines: list[str]) -> None:
        if self.live_line_count:
            print(f"\033[{self.live_line_count}A", end="")
        for line in lines:
            print(f"\033[2K{line}")
        self.live_line_count = len(lines)

    def _remove_live(self) -> None:
        if not self.live_line_count:
            return
        print(f"\033[{self.live_line_count}A", end="")
        for _ in range(self.live_line_count):
            print("\033[2K")
        print(f"\033[{self.live_line_count}A", end="")
        self.live_line_count = 0


def main() -> None:
    path = Path(config.STATUS_FILE)
    dashboard = Dashboard()
    try:
        print(f"{config.PROGRAM_NAME}  {config.PROGRAM_VERSION}")
        print("=" * 42)
        dashboard.print_model_configuration()
        while True:
            dashboard.update(read_status(path))
            time.sleep(config.CONTROL_REFRESH_INTERVAL)
    except KeyboardInterrupt:
        print()
        print("SolarTurtle control closed.")


if __name__ == "__main__":
    main()
