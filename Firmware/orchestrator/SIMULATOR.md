# Hexagon Simulator

A browser-based simulator for developing and testing patterns without the
physical art piece. It runs the **real** `orchestrator.send()` command
stream — from the Terminal, Patterns, or Tools pages — through a JS port
of the unit firmware, and renders the resulting surface relief as a
honeycomb heatmap.

The wire is physically one-way (the units can't report state back), so
this is necessarily a **predictive model**, not a mirror of telemetry —
see `src/sim/unit.js`'s header comment for exactly which firmware
behaviors it replicates (move merging, X vs. C vs. P's different effects
on the queue and tracked position, homing being blocking and deaf to
other commands, queue overflow, etc).

## Quick start (no Pi needed)

```bash
npm install          # first time only
npm run build         # builds the UI into dist/
python3 simhost/run.py
```

Then open **http://127.0.0.1:8080/admin/sim** in a browser.

`simhost/run.py` runs the actual `server/` + `server/patterns/` code —
nothing about routing, patterns, or the command tap is reimplemented. It
sets `HEXAGON_UART=none` so `orchestrator.py` writes to a null backend
instead of a real serial port (every command is already captured by the
tap before that write would happen), and redirects flash-filesystem paths
(`dist/`, `state.json`, `log.txt`) under `simhost/.fsroot/` so nothing
touches your real filesystem.

Options:

```bash
python3 simhost/run.py --port 9090      # different port
python3 simhost/run.py --host 0.0.0.0   # bind non-locally (e.g. to test from a phone)
```

While `simhost/run.py` is running, also start the Vite dev server in
another terminal if you're iterating on the UI itself:

```bash
npm run dev
```

(`vite.config.js`'s dev proxy points `/api` at `http://hexagons.local` (port
80, via nginx) by default — override the target there if you're proxying to
`simhost/run.py` on `127.0.0.1:8080` instead.)

## Using the sim

- Open the admin UI (`http://127.0.0.1:8080/admin`) and click **Sim ↗**
  in the tab bar — it opens the simulator in its own tab. Commands sent
  from any other tab (Terminal, macros, Patterns) show up there
  automatically — there's no direct connection between tabs; both are
  just reading the same server-side command tap (`GET /api/sim/tail`, fed
  by `orchestrator.send()`).
- Click a hexagon to open its **inspector**: run state, current angle,
  depth, RPM, queue depth (out of 64), whether it's moving or homing,
  whether it needs a home before its next move (set by `P`), and a
  queue-overflow counter.
- **Overlays** (sidebar) toggle unit ID / depth / angle / queue-depth
  labels drawn on each tile.
- **Transport bar** (bottom): play/pause, playback rate, and a scrub
  slider — drag it to replay any point in the current session. Scrubbing
  detaches from live tracking; click **Return to live** to resume
  following the real-time command stream.
- The **command log** (sidebar) shows every command seen, with anything
  a real unit would silently reject (bad syntax, out-of-range args)
  flagged in red.

## Running against the real hardware

The simulator works identically against the deployed Pi — it's just
reading `/api/sim/tail` and `/api/layout` from whatever server it's
served from. Open `http://hexagons.local/admin/sim` (after `./deploy.sh`)
to watch the sim mirror the real piece live, which is the fastest way to
sanity-check that the model still matches reality.

## Headless pattern checks

To sanity-check a pattern's command output (queue overflow, silently
rejected args, etc.) without a browser:

```bash
python3 -m simhost.selftest
```

This replays every pattern in `PATTERN_META` at its default and extreme
parameter values, plus `home_wave` explicitly (it's registry-only, so it's
otherwise invisible to a check driven by `PATTERN_META`), and asserts no
unit's queue would exceed the firmware's 64-slot capacity. It also checks
every mode under `server/modes/`: each mode's queue-safety validator at
default and extreme params, and the command syntax a single tick emits
(`server/modes/wave.py`'s `bounce_ms`/`validate`, via
`simhost/selftest.py`'s `check_modes()`) — since a mode runs forever,
there's no finite command stream to replay the way a pattern has, so this
exercises the same two things its tick loop relies on without ever
starting the background thread. It also checks `server/schedule.py` (time
parsing, the on/off-time-equal rejection, an unknown-timezone rejection,
the default timezone actually constructing, and `is_on_at()`'s truth table
for both window shapes including both boundary instants) and replay-
validates every mode's default/extreme params through the same
`resolve_params()` the scheduler's "on" transition uses, at
`MIN_RPM`/`DEFAULT_RPM`/`MAX_RPM` — the same speed-dependent failure mode
`check_modes()` guards against, since a param recorded at one configured
speed can fall outside another speed's queue-safety floor. All of the
above runs with zero threads and zero sleeping — `server/scheduler.py`'s
background supervisor thread is never started by this suite, same as
every mode's own background thread never is.

## Limitations

- `src/sim/bus.js` parses `M`, `A`, `H`, `N`, `X`, `P`, `S`, `W`, `G`, `C`,
  and `K`. It does not model `R` (batch repeat), `D` (staged coil
  release), or `I` (reprovisioning). A choreography using those still
  reaches the real bus fine; the sim just won't render their effect.
- The tap buffer is capped (see `tap.py`) — an extremely large burst
  sent while nobody has polled recently can trim old entries before the
  browser reads them. The sim detects this (`dropped` in the tail
  response) and resets its local model rather than replaying a gap; the
  command log will note it.
- The simulator predicts firmware behavior from the command stream; it
  has no way to detect real-world issues (a stalled motor, a bad
  connector, a slipping mechanism) since the bus is one-way.
