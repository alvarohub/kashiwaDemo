# Kashiwa Demo — SolarTurtle (local conversational node)

A self-contained demo of a small local AI node: a language model running on a
Raspberry Pi (via Ollama), a terminal chat, a phone-friendly web chat, sensor
loggers that give the node a sense of its own place, and an optional voice
loop. Everything runs locally — no cloud, no accounts.

## The shortest path

1. Prepare the Pi (§1) → 2. Get the code (§2) → 3. Install (`bash install.sh`, §3)
   → 4. Run (`./kami`, §4). Optional: autostart (§5), Wi-Fi access point (§6),
   voice (§7).

## What you need

- Raspberry Pi 5 — 8 GB+ recommended (4 GB works with a smaller model).
- Official 27 W (5 V / 5 A) USB-C power supply. Undervoltage = crashes.
- Active cooler or heatsink — sustained model load will throttle an uncooled
  Pi 5 (85 °C limit).
- microSD card, 32 GB+. The model needs ~1.5–2.5 GB.
- Raspberry Pi OS **64-bit** (Bookworm or newer). Ollama has no 32-bit ARM build.
- Internet during setup (packages + model download). Afterwards the node runs
  fully offline.
- A computer for SSH. No display needed.

## 1. Prepare the Pi (once)

1. Flash Raspberry Pi OS (64-bit) with Raspberry Pi Imager and set: hostname,
   username + password, your Wi-Fi, and **enable SSH**.
2. Boot the Pi, then from your computer:

   ssh <user>@<hostname>.local # or use the Pi's IP address

3. Update the system:

   sudo apt update && sudo apt full-upgrade -y

## 2. Get the code onto the Pi — three options

> **The node expects to live at `~/solarturtle`** (all scripts and services
> assume this name). Use that path in whichever option you pick.

### Option A — clone from GitHub (recommended)

On the Pi:

    git clone https://github.com/alvarohub/kashiwaDemo.git ~/solarturtle

(Needs internet on the Pi at this moment. To update later: `cd ~/solarturtle && git pull`.)

### Option B — push it from your computer over SSH

From the folder on your computer (SSH must be enabled; you need the Pi's address):

    rsync -av --exclude .git kashiwaDemo/ <user>@<pi-ip>:~/solarturtle/
    # or simply:
    scp -r kashiwaDemo <user>@<pi-ip>:~/solarturtle

There is also `push_to_pi.sh` in this repo: edit the `PI_HOST` line at the top
(your user@pi-address) and run it — it rsyncs this folder to the Pi and
restarts the node's services (if installed).

### Option C — through the SD card (last resort)

When the card is in a reader on your computer, only the **small FAT boot
partition** shows up (named `boot` or `bootfs`, ~512 MB). The main Linux
partition cannot be written from macOS/Windows. So:

1. Copy the `kashiwaDemo` folder onto the boot partition.
2. Boot the Pi and move it into place:

   sudo mv /boot/firmware/kashiwaDemo ~/solarturtle # Bookworm and newer

   # (on older systems the mount point is /boot/, not /boot/firmware/)

Clumsy by design — prefer Option A or B whenever a network path exists.

## 3. Install (one command)

    cd ~/solarturtle
    bash install.sh

This installs the system packages (python3-flask, requests, psutil, waitress…),
installs **Ollama**, and downloads the model named in `config.py`
(default: `llama3.2:1b`, ~1.3 GB — change `MODEL_NAME` there if you prefer
another, e.g. `phi3.5:latest` for higher quality / slower answers).
Takes a few minutes on a decent connection.

## 4. Run it

    ./kami

Starts the sensor loggers, the voice loop (if set up — §7), and the web chat
in your terminal. Open the shown address from any phone or computer on the
same network:

    http://<pi-ip>:8080

Ctrl+C stops everything. Just want to chat in the terminal instead?

    python3 main.py        # console mode without a display; fullscreen GUI with one
    python3 console.py     # explicit console chat

## 5. Optional: run it automatically at boot

    sudo bash set_autostart.sh on

Installs the node's background services (web + sensors + voice, adapted to
your username) and **starts the node right away**; from then on it starts by
itself at every boot, chat always at `:8080`. With no argument, the script
shows the current state. To undo (stops and removes everything, and cleans
up leftover processes):

    sudo bash set_autostart.sh off

Ollama is left running when you turn autostart off (`sudo systemctl stop
ollama` stops it). While autostart is on, `./start_kami.sh` / `./stop_kami.sh`
start and stop the node without removing anything.

## 6. Optional: Wi-Fi access point + captive portal

    sudo bash ap_up.sh ["SSID"] ["password"]    # phones join the Pi directly
    sudo bash portal_up.sh                      # joining pops the chat open

With the AP up, the chat is at `http://10.42.0.1:8080`. Tear down with
`portal_down.sh` / `ap_down.sh`. Note: the Pi's single Wi-Fi radio cannot be
an access point and a Wi-Fi client at the same time — do your downloads
first, or keep using Ethernet.

## 7. Optional: voice (USB microphone)

    bash setup_voice.sh

Creates `~/voice-venv` (faster-whisper + Silero VAD runtime) and downloads
the VAD model. Then use the microphone button on the chat page. A USB
microphone is required (this was developed with a ReSpeaker 4-mic array;
`voice/mic_setup.py` finds it and can log a calibration).

## 8. Optional: tools

    python3 control.py                 # live hardware + last-turn metrics over SSH
    python3 benchmark.py [models...]   # measure tokens/s on your Pi
    python3 lcd_gui.py                 # fullscreen GUI for an attached screen

## Adapt to your setup

| What                   | Where                                                                        | Notes                                                                           |
| ---------------------- | ---------------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| Your username          | — handled automatically                                                      | `set_autostart.sh` adapts the services + cron to whoever runs `sudo`        |
| The folder             | keep it as `~/solarturtle`                                                   | all scripts/services assume this name                                           |
| Pi address             | `PI_HOST` in `push_to_pi.sh`; the `ssh`/`rsync` examples above               | only where you connect                                                          |
| Wi-Fi AP name/password | `sudo bash ap_up.sh "Name" "Password"`, or the defaults inside `ap_up.sh`    | defaults are development values                                                 |
| HTTPS certificate      | SAN list in `make_cert.sh` (only if you use `python3 web_server.py --https`) | self-signed; each device accepts it once                                        |
| Model                  | `MODEL_NAME` in `config.py`, then re-run `install.sh`                        | default `llama3.2:1b`                                                           |
| The node's personality | `identity/identity.md`                                                       | template — give it a name and a place                                           |
| Voice paths            | candidates list in `voice/vad.py`                                            | first candidate is `models/silero_vad.onnx` → put it there via `setup_voice.sh` |

## Files your node will modify (normal)

These are the node's memory and logs — they change while it runs:

- `identity/journal.md` (a dated entry per day), `identity/turn_log.jsonl` (per turn)
- `sensors/*.md` (one line per interval), `sensors/mic_calibration.md` (when calibrating)
- `community/notes.md` (notes people leave on the chat page)
- `node_settings.json` (the toggles set from the chat page)

If you version your changes, expect `git status` to show these as modified.

## Troubleshooting

| Symptom                                  | Explanation / fix                                         |
| ---------------------------------------- | --------------------------------------------------------- |
| First answer takes ~10–30 s              | normal — the model loads into RAM on first use            |
| Model missing / Ollama errors            | `systemctl status ollama`; re-run `bash install.sh`       |
| Port 8080 already in use                 | an older run is still up — `sudo lsof -i :8080`           |
| Crashes under load / lightning bolt icon | undervoltage — use the official 27 W PSU                  |
| Temperature above ~80 °C                 | add active cooling (the Pi throttles at 85 °C)            |
| Phone can't reach the chat               | same network? use `hostname -I` on the Pi for its IP      |
| Mic shows "offline" on the page          | run `bash setup_voice.sh`; check the voice process output |


# Credits

This demo is a simplified, self-contained local AI node for the larger project "Hierarchical Civic AI". It's an example of the "level 1" or "public node" (running on a Raspberry Pi).

- **Seed code**: the original "SSAI" prototype — a minimal Tkinter + Ollama
  chat (~1,000 lines) — was written by Ido Hoffmann (with one commit by Yuri
  Klebanov) at **DLX - Design Lab, University of Tokyo** (Sept 2026):
  https://github.com/dlx-designlab/ssai
- **Lead development & architecture**: Alvaro Cassinelli.
- **Co-development**: GitHub Copilot, from 2026-09-17 onward.
