# Orchestrator

The central controller. Runs on a Raspberry Pi Zero W, serves a web UI, and drives the hexagon bus over UART. Deep setup and simulator details live in [`PI_SETUP.md`](PI_SETUP.md) and [`SIMULATOR.md`](SIMULATOR.md); this is the overview.

## Architecture

nginx is the only thing reachable from the network, on port 80. It serves the built UI (`dist/`) straight off disk and reverse-proxies `/api/*` to the app. The app itself, Flask running under Waitress, binds to `127.0.0.1:8080` only and is never reachable directly, even from another machine on the same LAN.

nginx and the app are installed as two independent systemd-managed pieces (`--install-nginx` and `--install-service`) since they change at different rates: routine deploys only ever touch the app, while nginx's config is effectively static after first setup. Waitress runs a single process with a small thread pool rather than gunicorn's multiple worker processes, deliberately, since more than one process opening the shared UART handle would interleave garbage onto the bus.

## The bus connection

The UART device is set by `HEXAGON_UART`, defaulting to `/dev/serial0` (the Pi's GPIO14/15 header UART). `HEXAGON_UART=none` runs against a null backend that discards every write instead of opening a port, which is what the simulator uses.

The deployed `deploy/hexagons.service` currently sets `HEXAGON_UART=/dev/ttyUSB0` instead of the default, because this installation's Pi has its onboard GPIO14/15 electrically damaged; an FTDI FT232R USB-to-serial adapter stands in until the board is replaced. If you're setting this up on a Pi with working GPIO, remove that override (or point it at `/dev/serial0` explicitly) and follow the GPIO UART setup in `PI_SETUP.md` instead.

Whichever device it is, the settings are fixed: 115200 baud, 8N1, one-way. The orchestrator writes; nothing reads back.

## Patterns vs. modes

Both send commands to the bus, but on different clocks:

- A **pattern** stages one finite burst into every unit's queue and fires it with a single `FFG`, then returns. Good for one-off or short-lived choreography.
- A **mode** runs on a background thread where the orchestrator itself is the clock: on every tick, it stages whatever should move next and fires it, so the units never drift relative to each other over a long run. Only one mode runs at a time; starting a pattern or hitting Stop kills the running mode.

Patterns: `water_drop`, `cascade`, `sweep`, `sparkle`, `pinwheel`, `bloom`, `rain`. (`home_wave`, the staggered full-installation homing sequence behind the persistent Home button, is registered the same way but deliberately left off the Patterns page, since it's a system operation rather than a choreography choice.)

Modes: `wave`, `random`, `life` (a hex-grid cellular automaton), `ripple`, and `auto`, which cycles through wave, ripple, and random on a timer for unattended operation, crossfading and re-homing between segments. `auto`'s timing lives in `server/modes/auto_config.py`.

Every mode and pattern's speed-dependent parameters (sweep timing, interval, generation length) are validated against whatever motor speed (`GET/POST /api/settings`) is actually configured, not a hardcoded assumption, so a parameter that's safe at one speed can't be replayed unsafely at another.

## Physical layout

`server/patterns/layout.py`'s `RINGS` list is the single source of truth for which unit ID sits where: concentric hexagonal rings, ring 0 the centre, each subsequent ring 6 units wider than the last. The current installation is 5 rings deep (61 units, `0x01`-`0x3D`). Every pattern and the simulator both read this file; nothing else hardcodes a unit count or position.

To adapt this to a different installation: edit `RINGS` to match your own wiring, and set `RING_START`/`RING_WINDING` so the simulator's on-screen ID overlay lines up with the physical wall. Only concentric hex-ring layouts are supported; a partial ring just fills the first positions in walk order.

## Frontend

Preact + Vite (not React). Routes, all client-side via `preact-router`:

| Route | Page |
| --- | --- |
| `/` | Calm visitor page: mode buttons, Home, Stop |
| `/touch` | Drag across the honeycomb to move the real units |
| `/settings` | Daily on/off schedule and default motor speed |
| `/admin` | Raw command terminal, with a macro drawer |
| `/admin/tools` | Motor speed, a unit-ID sweep/checker, Set Unit ID (bus `I` command), WiFi management |
| `/admin/patterns` | Pattern cards with parameter inputs |
| `/admin/modes` | Start/stop modes with parameters |
| `/admin/sim` | The browser simulator (see `SIMULATOR.md`) |

`npm run build` outputs to `dist/`, which is what nginx (or `simhost/`) actually serves.

## API

All of these sit behind nginx at port 80 in production; hitting `127.0.0.1:8080` directly only works on the Pi itself.

| Method | Path | What it does |
| --- | --- | --- |
| GET | `/api/health` | UART and clock status |
| POST | `/api/send` | Raw commands, one per line, `text/plain` |
| GET / POST | `/api/macros` | List / save a named macro |
| DELETE | `/api/macros/<name>` | Delete a macro |
| GET | `/api/patterns` | List patterns |
| POST | `/api/patterns/<name>` | Run one |
| POST | `/api/patterns/stop` | Stop whatever's running |
| GET | `/api/modes` | List modes |
| POST | `/api/modes/<name>` | Start one |
| POST | `/api/modes/stop` | Stop the running mode |
| GET | `/api/modes/status` | What's currently running |
| GET | `/api/sim/tail?since=N` | Recent command history, for the simulator |
| GET | `/api/layout` | Ring/coordinate geometry, for the simulator |
| GET / POST | `/api/network` | WiFi status / add a network (Pi only; reboots) |
| DELETE | `/api/network/<ssid>` | Forget a saved network |
| GET / POST | `/api/settings` | Default motor speed |
| GET / POST | `/api/schedule` | Daily on/off schedule |

## Persisted state

Everything the orchestrator remembers across a restart, macros, the default speed, the schedule, the last mode a human started, lives in one `state.json` under `HEXAGON_FS_ROOT`, written atomically. There's no database and no per-feature file.

## Running it locally, without a Pi

```bash
npm install && npm run build
python3 -m venv .venv && .venv/bin/pip install ruff -r requirements.txt
.venv/bin/python simhost/run.py        # serves on 127.0.0.1:8080, sandboxed HEXAGON_UART=none
python3 -m simhost.selftest            # queue-safety and command-syntax checks
```

Open `http://127.0.0.1:8080/admin/sim` for the visual simulator, which renders a heatmap of every unit's simulated angle by replaying the same command stream a real bus would see. See `SIMULATOR.md` for how it works and what it doesn't model (it doesn't simulate `R`, `D`, or `I`).

`npm run dev` runs Vite's dev server with hot reload, proxying `/api` to `hexagons.local` by default; point it at `127.0.0.1:8080` in `vite.config.js` if you're developing against `simhost` instead of a real Pi.

## Deploying to a Pi

```bash
./deploy.sh --bootstrap          # one-time OS setup: UART, WiFi/AP fallback, Tailscale (see PI_SETUP.md)
./deploy.sh --install-service    # one-time: install the systemd unit
./deploy.sh --install-nginx      # one-time: install nginx + the site config
./deploy.sh                      # routine: build, rsync, restart, health-check
./deploy.sh --watch              # rebuild and redeploy on every save
./deploy.sh --host 192.168.1.42  # target a specific IP instead of mDNS
```

`--bootstrap` and `--install-nginx` are interactive (they edit system config and ask for a sudo password once); everything else runs non-interactively over key-based SSH. A routine deploy excludes `state.json`, `log.txt`, `boot_credentials.json`, and a handful of other runtime/dev-only paths from the rsync, so redeploying never wipes saved state, and polls `/api/health` afterward to confirm the restart actually came back up.

## Python development

Python is linted and formatted with [ruff](https://docs.astral.sh/ruff/) (config in `pyproject.toml`); JS/JSX with ESLint and Prettier. The Husky pre-commit hook runs both against staged files and expects `.venv` to already exist (`python3 -m venv .venv && .venv/bin/pip install ruff -r requirements.txt`).

## Layout

```
orchestrator/
|-- main.py               entry point: sets HEXAGON_FS_ROOT, starts Flask/Waitress
|-- orchestrator.py        UART command module (pyserial) + the send lock
|-- boot_log.py            rotating file logger (log.txt)
|-- tap.py                 in-memory command history, feeds the simulator
|-- server/
|   |-- __init__.py        Flask app + route registration + serve()
|   |-- store.py           consolidated state.json persistence
|   |-- settings.py, schedule.py, scheduler.py, last_mode.py, macros.py
|   |                      thin typed facades over store.py
|   |-- modes/             mode registry, runner, and each mode's implementation
|   |-- patterns/          pattern registry, layout.py, and each pattern's implementation
|   +-- routes/            one module per API group, wired up in server/__init__.py
|-- src/                   Preact/Vite frontend source
|-- simhost/               dev harness that runs server/ off a real Pi (see SIMULATOR.md)
|-- deploy/                systemd units, nginx config, Pi bootstrap scripts
+-- deploy.sh              the deploy/bootstrap CLI described above
```
