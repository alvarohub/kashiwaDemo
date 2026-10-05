from __future__ import annotations

import queue
import tkinter as tk
import tkinter.font as tkfont
from collections.abc import Callable

import config


class ConversationGUI:
    """Simple full-screen conversation interface for the local Pi display."""

    def __init__(
        self,
        root: tk.Tk,
        on_submit: Callable[[str], bool],
        on_exit: Callable[[], None],
        on_generation_finished: Callable[[], None],
    ):
        self.root = root
        self.on_submit = on_submit
        self.on_exit = on_exit
        self.on_generation_finished = on_generation_finished
        self.stream_queue: queue.Queue[tuple[str, str | None]] = queue.Queue()
        self.follow_output = True

        self._configure_window()
        self._build_widgets()
        self._bind_keys()
        self.root.after(config.GUI_STREAM_POLL_MS, self._process_stream_queue)

    def _configure_window(self) -> None:
        self.root.title(config.PROGRAM_NAME)
        self.root.configure(bg=config.WINDOW_BACKGROUND)

        if config.FULLSCREEN:
            self.root.attributes("-fullscreen", True)
        else:
            self.root.geometry(f"{config.WINDOW_WIDTH}x{config.WINDOW_HEIGHT}")

        self.root.protocol("WM_DELETE_WINDOW", self.on_exit)

    def _build_widgets(self) -> None:
        font = (config.CHAT_FONT_FAMILY, config.CHAT_FONT_SIZE)

        input_frame = tk.Frame(self.root, bg=config.INPUT_BACKGROUND)
        input_frame.pack(fill="x", side="bottom")

        transcript_frame = tk.Frame(self.root, bg=config.TEXT_BACKGROUND)
        transcript_frame.pack(fill="both", expand=True)

        self.transcript = tk.Text(
            transcript_frame,
            wrap="word",
            bg=config.TEXT_BACKGROUND,
            fg=config.TEXT_FOREGROUND,
            insertbackground=config.TEXT_FOREGROUND,
            font=font,
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            padx=config.DISPLAY_MARGIN,
            pady=config.DISPLAY_MARGIN,
            state="disabled",
            takefocus=False,
        )
        self.scroll_indicator = tk.Canvas(
            transcript_frame,
            width=8,
            bg=config.TEXT_BACKGROUND,
            highlightthickness=0,
            borderwidth=0,
            takefocus=False,
        )

        self.scroll_indicator.pack(fill="y", side="right")
        self.transcript.pack(fill="both", expand=True)
        self.scroll_indicator.bind("<Configure>", self._update_scroll_indicator)
        ctx_font = tkfont.Font(
            self.root,
            family=config.CHAT_FONT_FAMILY,
            size=config.CHAT_FONT_SIZE,
            overstrike=not config.CONVERSATION_HISTORY,
        )
        self.ctx_indicator = tk.Label(
            transcript_frame,
            text="CTX",
            bg=config.TEXT_BACKGROUND,
            fg=config.TEXT_FOREGROUND,
            font=ctx_font,
        )
        self.ctx_indicator.place(
            relx=1.0,
            x=-config.DISPLAY_MARGIN,
            y=config.DISPLAY_MARGIN,
            anchor="ne",
        )

        self.input_prefix = tk.Label(
            input_frame,
            text=config.USER_PREFIX,
            bg=config.INPUT_BACKGROUND,
            fg=config.INPUT_FOREGROUND,
            font=font,
            padx=config.DISPLAY_MARGIN,
            pady=4,
        )
        self.input_prefix.pack(side="left")

        self.input_entry = tk.Entry(
            input_frame,
            bg=config.INPUT_BACKGROUND,
            fg=config.INPUT_FOREGROUND,
            insertbackground=config.INPUT_FOREGROUND,
            font=font,
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
        )
        self.input_entry.pack(side="left", fill="x", expand=True, padx=(0, config.DISPLAY_MARGIN), pady=4)
        self.input_entry.focus_set()

    def _bind_keys(self) -> None:
        self.input_entry.bind("<Return>", self._submit_from_entry)
        self.root.bind_all("<Up>", self._scroll_up)
        self.root.bind_all("<Down>", self._scroll_down)

        if config.TECHNICIAN_EXIT_SHORTCUT:
            self.root.bind_all(config.TECHNICIAN_EXIT_SHORTCUT, self._technician_exit)

    def _submit_from_entry(self, _event=None):
        prompt = self.input_entry.get().strip()
        if not prompt:
            return "break"

        accepted = self.on_submit(prompt)
        if accepted:
            self.input_entry.delete(0, "end")
        return "break"

    def add_user_message(self, text: str) -> None:
        self._append_transcript(f"{config.USER_PREFIX}{text}\n")

    def start_model_message(self) -> None:
        self._append_transcript(config.MODEL_PREFIX)

    def queue_model_chunk(self, chunk: str) -> None:
        self.stream_queue.put(("chunk", chunk))

    def queue_model_complete(self) -> None:
        self.stream_queue.put(("complete", None))

    def queue_model_error(self, text: str) -> None:
        self.stream_queue.put(("error", text))

    def clear_conversation(self) -> None:
        self.transcript.configure(state="normal")
        self.transcript.delete("1.0", "end")
        self.transcript.configure(state="disabled")
        self.follow_output = True
        self._update_scroll_indicator()

    def _process_stream_queue(self) -> None:
        # Coalesce chunks that arrived during the same GUI tick. This keeps the
        # streaming appearance while avoiding one Text redraw per model chunk.
        pending_text: list[str] = []

        try:
            while True:
                event_type, payload = self.stream_queue.get_nowait()

                if event_type == "chunk" and payload is not None:
                    pending_text.append(payload)
                    continue

                if pending_text:
                    self._append_transcript("".join(pending_text))
                    pending_text.clear()

                if event_type == "error" and payload is not None:
                    self._append_transcript(payload)
                    self._finish_model_spacing()
                    self.on_generation_finished()
                elif event_type == "complete":
                    self._finish_model_spacing()
                    self.on_generation_finished()

        except queue.Empty:
            pass

        if pending_text:
            self._append_transcript("".join(pending_text))

        if self.root.winfo_exists():
            self.root.after(config.GUI_STREAM_POLL_MS, self._process_stream_queue)

    def show_model_error(self, text: str) -> None:
        """Show a model error immediately when already running on the GUI thread."""
        self._append_transcript(text)
        self._finish_model_spacing()

    def _finish_model_spacing(self) -> None:
        # N blank lines between the model response and the next YOU line require
        # N + 1 newline characters after the response text.
        newline_count = config.BLANK_LINES_AFTER_MODEL + 1
        self._append_transcript(
            f"\n{config.CONVERSATION_SEPARATOR}\n" + "\n" * newline_count
        )

    def _append_transcript(self, text: str) -> None:
        self.transcript.configure(state="normal")
        self.transcript.insert("end", text)
        self.transcript.configure(state="disabled")

        if self.follow_output:
            self.transcript.see("end")
        self._update_scroll_indicator()

    def _update_scroll_indicator(self, _event=None) -> None:
        top, bottom = self.transcript.yview()
        position = (top + bottom) / 2
        height = self.scroll_indicator.winfo_height()
        radius = 3
        margin = radius
        center_y = margin + position * max(height - 2 * margin, 0)
        center_y = max(radius, min(center_y, max(height - radius, radius)))

        self.scroll_indicator.delete("all")
        self.scroll_indicator.create_oval(
            4 - radius,
            center_y - radius,
            4 + radius,
            center_y + radius,
            fill="white",
            outline="white",
        )

    def _scroll_up(self, _event=None):
        self.follow_output = False
        self.transcript.yview_scroll(-config.SCROLL_LINES_PER_KEYPRESS, "units")
        self._update_scroll_indicator()
        return "break"

    def _scroll_down(self, _event=None):
        self.transcript.yview_scroll(config.SCROLL_LINES_PER_KEYPRESS, "units")
        self.root.update_idletasks()
        _, bottom = self.transcript.yview()
        if bottom >= 0.999:
            self.follow_output = True
            self.transcript.see("end")
        self._update_scroll_indicator()
        return "break"

    def _technician_exit(self, _event=None):
        self.on_exit()
        return "break"
