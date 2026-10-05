# Architecture — what is actually implemented

One Raspberry Pi = one node. Everything runs on the Pi, fully offline; the
only separate moving part is Ollama (the local model server, also on the Pi).

```
  phone / laptop browser ──HTTP+SSE──► web_server.py (:8080) ─┐
  SSH terminal: console.py / main.py (or Tkinter GUI) ────────┼──► llm_manager.py ──► Ollama (:11434)
  USB mic: voice/talk.py (whisper + VAD) ─────────────────────┘        │
          │ turn + metrics                                    (streaming tokens)
          ▼
  /tmp/solarturtle_status.json   (live state, read by the web page + control.py)

  sensors_boot.py ──spawns──► loggers/*.py ──append lines──► sensors/*.md
                                                                  │
  memory folders (read into the prompt on demand):               │
  identity/ (who it is) · sensors/ (latest readings) ◄───────────┘
  · community/ (people's notes)

  node_settings.json — ONE shared settings file for ALL frontends
```

## Components (the files)

**Frontends** — separate processes, same model:

- `web_server.py` — Flask (+ waitress) server; the single-page chat is
  `static/chat.html` (streaming over SSE); status API; `/api/note` appends
  community notes; also answers captive-portal probes.
- `console.py` — screenless chat over SSH; `main.py` detects "no display"
  and starts it automatically (with a display it opens a Tkinter GUI;
  `lcd_gui.py` is the fullscreen variant).
- `voice/talk.py` — optional voice loop: `arecord` → Silero VAD
  (`voice/vad.py`) → faster-whisper → the same `LLMManager`. Controlled from
  the web page (off / wake word / push-to-talk) through `node_settings.json`
  plus a heartbeat file. Needs `setup_voice.sh` and a USB microphone.
- `control.py` — small SSH dashboard: live hardware + last turn (reads the
  status file).

**Core** (shared by all frontends):

- `llm_manager.py` — streaming client for Ollama; builds the prompt (system
  prompt + selected memory categories + history); records metrics
  (time-to-first-token, tokens/s).
- `reports_reader.py` — folds the memory folders into a prompt digest:
  identity files; per sensor: description + units + the LATEST line;
  community notes. Categories are toggled per question (web UI) or
  auto-detected from keywords (`/sensors ...` forces one).
- `node_settings.py` — the shared settings file (`node_settings.json`):
  memory toggles, model override, voice flags, "speak/stop" requests.
- `status_store.py` — writes `/tmp/solarturtle_status.json` (hardware +
  latest turn) for every reader.
- `hardware_monitor.py` — CPU / RAM / temperature sampler.

**Sensing**:

- `sensors.json` — the registry: what to run, how often, where it logs.
- `sensors_boot.py` — spawns one crash-isolated supervisor per active entry
  (see `SENSORS.md`).
- `loggers/` — the logger scripts; `journal_writer.py` (cron, 23:55) writes
  the node's daily journal entry.

**Ops**:

- `install.sh` (setup) · `kami` (run now) · `install_autostart.sh`
  (optional boot services) · `start_kami.sh` / `stop_kami.sh` (control the
  installed services) · `ap_up.sh` / `ap_down.sh` (Wi-Fi access point) ·
  `portal_up.sh` / `portal_down.sh` (captive portal) · `make_cert.sh`
  (+ `web_server.py --https`) · `push_to_pi.sh` (push from a laptop).

## Data flows

**A question.** Frontend → `LLMManager.stream_response()` → Ollama streams
tokens → the frontend renders them; `status_store` records the turn +
metrics; the web page sees it via `/api/status`.

**A sensing tick.** `sensors_boot.py` runs each active logger every N
seconds → the logger appends one line to its file in `sensors/`. When the
"sensors" memory category is on, the prompt gets each sensor's description +
units + LATEST line — never the whole log (small models misread long logs).

**Nightly journal.** Cron runs `journal_writer.py`, which writes a dated
entry into `identity/journal.md` — the node's own continuity.

## Design notes

- **Files, not a database.** All memory and logs are plain markdown/text —
  they are the interface, readable by humans and the model.
- **One model, one prefix.** A single Ollama model + one shared system prompt
  keeps the prompt-prefix cache warm; switching frontends (phone ↔ voice)
  does not pay a re-evaluation.
- **Degrade gracefully.** No display → console mode; missing packages →
  clear message (re-run `install.sh`); no voice venv → voice simply not
  started.
