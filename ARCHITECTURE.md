# Architecture — what is actually implemented

One Raspberry Pi = one node. Everything runs on the Pi, fully offline; the
only separate moving part is Ollama (the local model server, also on the Pi).

## The big picture

Read it top to bottom: people reach a frontend; every frontend embeds the
same core; the core asks the model; the model sees the memory files; the
sensing loggers keep those files fresh.

```
┌─ PEOPLE & DEVICES ────────────────────────┐
│  phone/laptop · SSH terminal · USB mic    │
└───────────────┬───────────────────────────┘
                │ HTTP+SSE · stdin/stdout · audio
                ▼
┌─ FRONTENDS (one process each) ────────────┐     ┌─ LIVE STATE ─────────────────┐
│  web_server.py (:8080)                    │     │  /tmp/solarturtle_status.json│
│  console.py  (main.py adds the GUI)       │────►│  written by every frontend;  │
│  voice/talk.py  (optional)                │     │  read by the web page and    │
└───────────────┬───────────────────────────┘     │  control.py                  │
                │ every frontend embeds the CORE  └──────────────────────────────┘
                ▼
┌─ CORE (shared code inside every frontend) ┐     ┌─ MEMORY (plain text) ────────┐
│  llm_manager.py    (the only model client)│◄────│  identity/   (who it is)     │
│  reports_reader.py (files → prompt digest)│     │  sensors/    (latest lines)  │
│  node_settings.py  (one shared settings)  │     │  community/  (people's notes)│
│  status_store.py   (the live-state writer)│     └──────────────▲───────────────┘
└───────────────┬───────────────────────────┘                    │ appends
                │ HTTP POST /api/chat                            │
                ▼                                                │
┌─ THE MODEL ───────────────────────────────┐     ┌─ SENSING─────┴───────────────┐
│  Ollama :11434 — its own service          │     │  sensors.json (registry) →   │
│  (the only piece that is not our Python)  │     │  sensors_boot.py →           │
└───────────────────────────────────────────┘     │  loggers/*.py → sensors/*.md │
                                                  └──────────────────────────────┘

┌─ OPS (scripts, all at the repo root) ────────────────────────────────────────┐
│  install.sh · kami · set_autostart.sh · start/stop_kami.sh                   │
│  ap/portal_up/down · make_cert.sh · push_to_pi.sh                            │
└──────────────────────────────────────────────────────────────────────────────┘
```

## The blocks, one by one

### Frontends — how people reach the node

- `web_server.py` — Flask (+ waitress) server; the single-page chat is
  `static/chat.html` (streaming over SSE); status API; `/api/note` appends
  community notes; also answers captive-portal probes.
- `console.py` — screenless chat over SSH; `main.py` starts it automatically
  when there is no display (with a display it opens a Tkinter GUI;
  `lcd_gui.py` is the fullscreen variant).
- `voice/talk.py` — optional voice loop: `arecord` → Silero VAD
  (`voice/vad.py`) → faster-whisper → the same `LLMManager`. Switched
  on/off from the web page via `node_settings.json` + a heartbeat file;
  needs `setup_voice.sh` and a USB microphone.

A question, end to end:

```
  frontend ──► LLMManager.stream_response() ──► Ollama ──► token stream ──► rendered live
```

### Core — the same small library inside every frontend

- `llm_manager.py` — streaming client for Ollama; assembles the prompt
  (system prompt + selected memory categories + history); records metrics
  (time-to-first-token, tokens/s).
- `reports_reader.py` — folds the memory folders into the prompt digest:
  identity files; per sensor its description + units + the LATEST line;
  community notes. Categories are toggled per question (web UI) or
  auto-detected from keywords (`/sensors ...` forces one).
- `node_settings.py` — reads/writes `node_settings.json`, the ONE shared
  settings file (memory toggles, model override, voice flags) written by the
  web page and obeyed by all frontends.
- `status_store.py` — writes `/tmp/solarturtle_status.json` (hardware +
  latest turn).
- `hardware_monitor.py` — CPU / RAM / temperature sampler.

```
  memory files ──► reports_reader ──► system prompt ──► Ollama
```

### The model — Ollama

Its own systemd service on `127.0.0.1:11434`. Our code never touches model
files: it POSTs a prompt and gets back a token stream. See the design notes
below for why the model is a server, not a library.

### Memory — plain files (the node's memory)

- `identity/` — `identity.md` (who the node is) + `journal.md` (dated entries
  written nightly by `journal_writer.py`).
- `sensors/` — one file per sensor; only the latest line is read into the
  prompt.
- `community/` — notes people leave on the chat page.

Files, not a database — humans and the model read the same text.

### Sensing — where readings come from

`sensors.json` (the registry: what to run, how often, where it logs) →
`sensors_boot.py` (one crash-isolated supervisor per active entry) →
`loggers/*.py` (each reads its hardware and appends one line) →
`sensors/*.md` (a new line every N seconds).

When the "sensors" memory category is on, the prompt gets each sensor's
description + units + LATEST line — never the whole log (small models
misread long logs).

### Live state — the bulletin board

`/tmp/solarturtle_status.json` — "I'm alive, here's the current
question/metrics", written atomically by `status_store.py` in every
frontend; read (display only!) by the web page and `control.py`. The voice
loop's heartbeat is a separate small file (`/tmp/solarturtle_voice.json`).
`control.py` is the little SSH dashboard that renders this.

### Ops — installing and running

`install.sh` (setup) · `kami` (run now) · `set_autostart.sh on|off` (optional
boot services) · `start_kami.sh` / `stop_kami.sh` (control the installed
services) · `ap_up.sh` / `ap_down.sh` (Wi-Fi access point) · `portal_up.sh` /
`portal_down.sh` (captive portal) · `make_cert.sh` (+ `web_server.py --https`)
· `push_to_pi.sh` (push from a laptop).

## Extending the node — adding a new logger

The point of the design: a new sensor is **a new small file + one registry
line**. Nothing else changes — the chat core is untouched.

1. **Write the logger** — copy `loggers/sensor_pivitals.py` to e.g.
   `loggers/sensor_light.py`. The header lines (`# description:`, `# units:`)
   are what the model reads; then append one readable line per reading.
2. **Register it** — add an entry to `sensors.json` (id, `logger`,
   `log_file`, `interval_seconds`, `active: true`).
3. **Start it** — `sudo systemctl restart solarturtle-sensors` (or reboot),
   then check: `python3 sensors_boot.py --status` lists it and
   `tail -f sensors/sensor_light.md` shows new lines.

Now ask the chat about it ("how bright is it?") — the sensors category
switches on by itself. Full walkthrough with code: **[SENSORS.md](SENSORS.md)**.

## Design notes

- **Files, not a database.** All memory and logs are plain markdown/text —
  they are the interface, readable by humans and the model.
- **One model, one prefix.** A single Ollama model + one shared system prompt
  keeps the prompt-prefix cache warm; switching frontends (phone ↔ voice)
  does not pay a re-evaluation.
- **Degrade gracefully.** No display → console mode; missing packages →
  clear message (re-run `install.sh`); no voice venv → voice simply not
  started.
