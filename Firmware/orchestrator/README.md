# Hexagon Art Piece — Orchestrator Firmware

Central controller for the hexagon art piece.  
Runs on a **Raspberry Pi Zero W (v1.1) with Raspberry Pi OS Lite**, serves a
web UI, and sends UART commands to hexagon units over a one-way bus at
115200 baud.

---

## Hardware Notes

This firmware targets a **Raspberry Pi Zero W v1.1** running Raspberry Pi OS
Lite. The bus wiring is unchanged from the previous ESP32 controller — 3.3V
logic, TX-only, 115 200 baud — connected to the Pi's GPIO UART (pin 8 /
GPIO14, `/dev/serial0`).

> **The Zero W's PL011 UART is wired to Bluetooth by default**, leaving
> `/dev/serial0` mapped to the mini-UART, whose baud rate is tied to the VPU
> core clock and unreliable at 115 200. It also runs a login console on that
> port by default, which would spray boot/login text down the hexagon bus.
> Both are fixed in one-time OS setup below.

---

## Architecture

**nginx is the only thing reachable from the network, on port 80.** It
serves `dist/` (the built UI) straight off disk and reverse-proxies
`/api/*` to the app. The app itself — Flask, running under the Waitress
WSGI server — binds to `127.0.0.1:8080` only; it's never reachable
directly, even from another machine on the same LAN.

```
Browser ──:80──> nginx ──┬── /            → dist/ (static files, off disk)
                          └── /api/*       → 127.0.0.1:8080 (Flask/Waitress)
                                                  │
                                                  └── orchestrator.py ──UART──> hexagon bus
```

Two systemd-managed pieces, installed independently (`--install-service`
for the app, `--install-nginx` for nginx) since they change at different
rates: routine deploys only ever touch the app (rsync + restart
`hexagons`), while nginx's config is effectively static after first setup.

---

## First-Time Setup

These steps are only needed once per Pi. After this, all deploys happen over
SSH.

### 1 — Flash and boot Raspberry Pi OS Lite

Use [Raspberry Pi Imager](https://www.raspberrypi.com/software/) to flash
**Raspberry Pi OS Lite**. In the imager's advanced options, set the hostname
to `hexagons`, enable SSH (password auth — `--bootstrap` below needs
`ssh-copy-id` to already have run), and configure at least one WiFi network
so first boot can reach `apt`.

```bash
ssh-copy-id pi@hexagons.local
```

### 2 — OS bring-up: UART, USB gadget SSH, WiFi/AP fallback, Tailscale

Create `boot_credentials.json` in this repo's root (gitignored, never
committed):

```json
{
  "wifi_networks": [
    { "ssid": "...", "password": "...", "priority": 100 },
    { "ssid": "...", "password": "..." }
  ],
  "ssh": { "username": "pi", "password": "..." },
  "ap": { "ssid": "hexagon-ap" }
}
```

Then, from this repo:

```bash
./deploy.sh --bootstrap   # one-time: UART, USB gadget, WiFi/AP, tailscaled — see PI_SETUP.md
```

This hands the PL011 UART back from Bluetooth, disables the serial login
console, stages USB gadget SSH (fallback: `ssh pi@10.42.0.1` over a USB
cable, no WiFi needed), creates a NetworkManager profile per network in
`boot_credentials.json`, sets up an open (no-password — see `PI_SETUP.md`
§6 for why) `hexagon-ap` access point that comes up automatically if no
known WiFi network is found at boot, and installs + enables Tailscale
(not authenticated — run `sudo tailscale up` yourself whenever you're
ready). It's interactive (prompts for the sudo password once) and
idempotent — safe to re-run after a failure or a credentials change.

It ends by telling you a reboot is required (it is, the first time):

```bash
ssh pi@hexagons.local 'sudo reboot'
```

**Verify before wiring up the piece** — loop GPIO14 (pin 8) to GPIO15
(pin 10) with a jumper wire and confirm a clean echo:

```bash
ssh pi@hexagons.local "python3 -c \"import serial; s=serial.Serial('/dev/serial0',115200,timeout=1); s.write(b'FFG\r\n'); print(s.read(5))\""
# should print: b'FFG\r\n'
```

Garbage output means the Bluetooth overlay didn't take; boot/login text
means the serial console is still enabled. See `PI_SETUP.md` §2 for the
full gotcha list (in particular: this check is meaningless before the
reboot above — config file edits don't affect the running kernel).

### 3 — Install the app + nginx, and do the first deploy

```bash
npm install                     # one-time, for the Vite build
./deploy.sh --install-service   # one-time: installs + enables hexagons.service
./deploy.sh --install-nginx     # one-time: installs nginx + Flask/Waitress + the site config
./deploy.sh                     # builds the UI, syncs files, restarts the service
```

`--install-nginx` is interactive (asks for your sudo password once) —
everything else runs non-interactively over the SSH key. See
[Architecture](#architecture) below for why nginx is in the picture at all.

### 4 — Open the UI

```
http://hexagons.local
```

---

## Subsequent Deploys

```bash
# One-shot — builds UI, rsyncs files, restarts the service
./deploy.sh

# Watch mode — rebuilds and redeploys on every save
./deploy.sh --watch

# Target a specific IP instead of mDNS
./deploy.sh --host 192.168.1.42
```

Watch mode requires `fswatch` (macOS: `brew install fswatch`) or
`inotifywait` (Linux: `apt install inotify-tools`).

The deploy script:

1. Runs `npm run build` (Vite → `dist/`)
2. `rsync`s the repo to `/opt/hexagons` on the Pi (excluding `macros.json`
   and `log.txt` — those are runtime state, not source, and would otherwise
   get wiped by `--delete` on every deploy)
3. `ssh`es in and runs `sudo systemctl restart hexagons`
4. Polls `GET http://<host>/api/health` (through nginx, port 80 — the app
   itself isn't reachable from here) until the server responds (up to 20 s)

nginx itself needs no action on a routine deploy — it reads `dist/`
straight off disk, so new content is live the moment step 2 finishes. Only
a change to `deploy/nginx.hexagons.conf` needs `./deploy.sh --install-nginx`
re-run.

---

## Python Development Environment

Firmware Python files are linted and formatted with **[ruff](https://docs.astral.sh/ruff/)**.

### Set up the venv

```bash
python3 -m venv .venv
.venv/bin/pip install ruff -r requirements.txt
```

### Lint & format manually

```bash
# Check
.venv/bin/ruff check server/ *.py

# Auto-fix + format
.venv/bin/ruff check --fix server/ *.py
.venv/bin/ruff format server/ *.py
```

Ruff config lives in `pyproject.toml`.

### Pre-commit hook

The Husky pre-commit hook (`.husky/pre-commit`) automatically:

1. Runs **ESLint + Prettier** on staged JS/JSX files.
2. Runs **ruff format + ruff check --fix** on staged `.py` files and re-stages them.

Requires the `.venv` to exist — run the setup step above once per clone.

---

## Project Structure

```
orchestrator/
├── main.py               Entry point: sets HEXAGON_FS_ROOT, starts Flask/Waitress
├── boot_log.py            File logger (log.txt) with rotation
├── orchestrator.py        UART command module (pyserial) + the send() lock
├── server/                Application layer (Flask)
│   ├── __init__.py        Flask app, route registration, serve() (Waitress)
│   ├── macros.py          Macro JSON persistence (/macros.json) + its lock
│   ├── modes/              Mode registry (server/modes/__init__.py) + runner.py + wave.py
│   └── routes/
│       ├── send.py        POST /api/send
│       ├── macros.py      GET/POST/DELETE /api/macros
│       ├── system.py      GET /api/health
│       ├── patterns.py    GET /api/patterns, POST /api/patterns/<name>, /stop
│       ├── modes.py       GET /api/modes, POST /api/modes/<name>, /stop, GET /status
│       └── sim.py         GET /api/sim/tail, GET /api/layout
├── tap.py                 In-memory command tap, feeds the simulator
├── src/                   Vite/Preact web UI source
│   ├── pages/SimplePage.jsx  Calm root UI (/) — mode buttons, Home, Stop
│   └── sim/                JS firmware model + polling client for the simulator
├── simhost/                CPython dev harness — runs server/ off-device (see SIMULATOR.md)
├── dist/                   Built UI (generated — served by nginx on the Pi)
├── deploy/
│   ├── hexagons.service    systemd unit for the app, installed by deploy.sh --install-service
│   └── nginx.hexagons.conf nginx site config, installed by deploy.sh --install-nginx
├── deploy.sh               rsync + systemd deploy script
├── requirements.txt        Python deps (pyserial, flask, waitress) for local dev venvs
├── package.json            Node dependencies (Vite, Preact, ESLint)
├── pyproject.toml          Ruff config
└── .venv/                  Python venv (git-ignored)
```

---

## API Reference

All endpoints are on **port 80**, via nginx (see
[Architecture](#architecture)) — the app itself binds to `127.0.0.1:8080`
and is never reached directly.

| Method   | Path                   | Description                               |
| -------- | ---------------------- | ----------------------------------------- |
| `GET`    | `/*`                   | Static files from `dist/`                 |
| `GET`    | `/api/health`          | Liveness + UART status (JSON)             |
| `POST`   | `/api/send`            | Send UART commands (`text/plain` body)    |
| `GET`    | `/api/macros`          | List all macros                           |
| `POST`   | `/api/macros`          | Create/update macro `{name, commands}`    |
| `DELETE` | `/api/macros/<name>`   | Delete a macro by name                    |
| `GET`    | `/api/patterns`        | List available patterns                   |
| `POST`   | `/api/patterns/<name>` | Run a named pattern                       |
| `POST`   | `/api/patterns/stop`   | Emergency stop (FFX + FFC)                |
| `GET`    | `/api/modes`           | List available modes                      |
| `POST`   | `/api/modes/<name>`    | Start a continuously-running mode         |
| `POST`   | `/api/modes/stop`      | Stop the running mode (+ FFX + FFC)       |
| `GET`    | `/api/modes/status`    | Which mode (if any) is currently running  |
| `GET`    | `/api/sim/tail`        | Recent command tap history (simulator)    |
| `GET`    | `/api/layout`          | Unit ring/coordinate topology (simulator) |
| `GET`    | `/api/settings`        | Default motor speed + allowed range       |
| `POST`   | `/api/settings`        | Set default motor speed                   |
| `GET`    | `/api/schedule`        | Daily on/off schedule + timezone + clock state |
| `POST`   | `/api/schedule`        | Patch the daily schedule (partial)        |

---

See `AGENTS.md` for the full protocol reference and architecture overview,
and `SIMULATOR.md` for developing patterns locally without a Pi.
