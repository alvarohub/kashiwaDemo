# SolarTurtle node configuration
# Keep prototype parameters here so the main program logic stays simple.

PROGRAM_NAME = "SolarTurtle"
PROGRAM_VERSION = "V4"
SYSTEM_PROMPT = """Answer the user's question directly with the shortest response that provides a complete and correct answer.

Prefer one sentence when it is sufficient. As a general rule, do not exceed 50 words. This is a maximum, not a target.

As soon as a sufficient answer has been given, stop immediately. Do not repeat information or add explanations, examples, related topics, or suggestions that are not necessary to answer the question.

If you do not know the answer, say so briefly and do not guess.

If essential information is missing for a reliable answer, ask one short clarifying question and stop.

Ignore minor spelling or typing errors when the user's meaning is clear.
"""

# Local LLM
# Measured on a Raspberry Pi 5 (16GB): phi3.5 ≈ 5.2 tok/s (memory-bandwidth
# ceiling). llama3.2:1b is ~3-4x faster — better for a conversational/voice UX.
MODEL_NAME = "llama3.2:1b"
# MODEL_NAME = "phi3.5:latest"  # higher quality, ~5 tok/s
OLLAMA_API_BASE = "http://127.0.0.1:11434/api"
NUM_PREDICT = 150
CONVERSATION_HISTORY = True
REQUEST_TIMEOUT_SECONDS = 10
GENERATION_SHUTDOWN_TIMEOUT = 1.0

# Hardware monitoring / SSH control interface
# The main process samples hardware every 15 seconds.
HARDWARE_SAMPLE_INTERVAL = 15.0
STATUS_FILE = "/tmp/solarturtle_status.json"
# The Dashboard polls status more often than hardware is sampled so questions
# and generation changes appear promptly without triggering extra samples.
CONTROL_REFRESH_INTERVAL = 0.5
STATUS_STALE_AFTER_SECONDS = 45

# GUI
FULLSCREEN = True
WINDOW_WIDTH = 480
WINDOW_HEIGHT = 320
WINDOW_BACKGROUND = "black"
CONVERSATION_SEPARATOR = "------------------------------"
TEXT_BACKGROUND = "black"
TEXT_FOREGROUND = "white"
INPUT_BACKGROUND = "black"
INPUT_FOREGROUND = "white"
CHAT_FONT_FAMILY = "DejaVu Sans"
CHAT_FONT_SIZE = 16
DISPLAY_MARGIN = 8
GUI_STREAM_POLL_MS = 40
SCROLL_LINES_PER_KEYPRESS = 2

# Conversation format
USER_PREFIX = "ME: "
MODEL_PREFIX = "MODEL: "
BLANK_LINES_AFTER_MODEL = 2

# Technician-only exit shortcut.
# This is kept here so it can be changed without editing application logic.
TECHNICIAN_EXIT_SHORTCUT = "<Control-Alt-KeyPress-q>"

# Linux hardware paths
TEMPERATURE_PATH = "/sys/class/thermal/thermal_zone0/temp"
