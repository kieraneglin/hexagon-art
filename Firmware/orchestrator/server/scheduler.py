"""
server/scheduler.py — background supervisor for the daily on/off schedule
(server/schedule.py).

Same shape as server/modes/runner.py's supervisor: a daemon thread ticking
on a short fixed interval, never time.sleep. What it does with each tick is
different from a mode, though — a mode re-syncs the bus on every tick; this
only ever *acts* on a change in desired state.

Edge-triggered, not level-triggered. Every tick recomputes "should the piece
be on right now?" (schedule.is_on_at()), but only takes any bus action when
that answer actually flips from the previous tick. This is the property
that makes the schedule safe to run at all: without it, a visitor who
presses Stop at 3pm (well inside an "on" period) would find the piece
started right back up on the very next 30-second tick, since "should be on"
never stopped being true. Edge-triggering means the schedule only ever
speaks up at the two moments its opinion changes, and stays out of the way
the rest of the time.

Fails on, not off: the scheduler does nothing at all — no edge is ever
computed or recorded — while the configured schedule is disabled, or while
schedule.clock_synced() says the clock can't be trusted yet (see that
function's docstring for why the Pi Zero W's lack of an RTC makes this a
real, not theoretical, condition). "The art keeps running when it should
have gone quiet" is a far better failure mode on a bus with no operator
watching than "the art stays dark all day for a reason nobody on-site can
diagnose."
"""

import threading

import orchestrator
from server import last_mode, schedule, settings
from server.modes import REGISTRY, resolve_params
from server.modes import runner as mode_runner
from server.modes.auto_config import HOME_SETTLE_MS
from server.patterns.helpers import settle_ms

_TICK_S = 30

_lock = threading.Lock()
_thread: threading.Thread | None = None
_stop_event: threading.Event | None = None

# None means "next eligible tick should be treated as an initial edge" —
# set whenever the scheduler is disabled/unsynced, and on the very first
# tick after the thread starts. Otherwise "on"/"off", the desired state as
# of the last tick that actually evaluated one.
_last_desired: str | None = None


def start() -> None:
    """Start the supervisor thread. Safe to call once at boot
    (main.py/simhost.run.py); a second call is a no-op — this isn't a
    single-mode-at-a-time resource like server/modes/runner.py, just one
    background loop that should exist exactly once per process."""
    global _thread, _stop_event
    with _lock:
        if _thread is not None:
            return
        stop_event = threading.Event()
        thread = threading.Thread(target=_run, args=(stop_event,), daemon=True)
        _thread = thread
        _stop_event = stop_event
    thread.start()


def status() -> dict:
    """Return {"desired": "on"|"off"|None} — the scheduler's current
    opinion, merged into GET /api/schedule. None means it hasn't evaluated
    yet (disabled, or clock not synced) — distinct from "off", which is a
    real, acted-upon state."""
    return {"desired": _last_desired}


def _run(stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        try:
            _tick(stop_event)
        except Exception as exc:
            # A one-way bus has no operator watching for a stack trace —
            # an uncaught exception here would silently end scheduling for
            # the rest of the deploy with zero symptom. boot_log survives
            # in log.txt for a post-mortem; the loop itself must carry on
            # to the next tick regardless.
            from boot_log import log

            log(f"scheduler: tick failed: {exc!r}")
        stop_event.wait(_TICK_S)


def _tick(stop_event: threading.Event) -> None:
    global _last_desired

    cfg = schedule.get()
    if not cfg["enabled"] or not schedule.clock_synced():
        _last_desired = None
        return

    import datetime

    now = datetime.datetime.now(schedule.tz())
    desired = "on" if schedule.is_on_at(now) else "off"

    if _last_desired is None:
        # Initial edge — either the process just started, or scheduling was
        # just (re-)armed. Consult what was last actually applied to the
        # hardware: if it already matches, a routine restart mid-off-period
        # (e.g. a deploy at 11pm) shouldn't re-run a full home+power-down
        # broadcast to 61 units for no observable reason. A never-recorded
        # last_applied (fresh install, or the schedule just got enabled)
        # always acts, since "unknown" can't be assumed to already match.
        if cfg["last_applied"] == desired:
            _last_desired = desired
            return
    elif _last_desired == desired:
        return

    if desired == "on":
        _turn_on()
    else:
        _turn_off(stop_event)

    schedule.set_last_applied(desired)
    _last_desired = desired


def _turn_on() -> None:
    """Replay the last mode a human explicitly started, falling back to
    "auto" if nothing was ever recorded or if replay fails validation —
    e.g. a param recorded at one configured motor speed can fall outside
    another speed's queue-safety floor (every mode's validate() is
    speed-dependent — see resolve_params() in server/modes/__init__.py). A stale
    recorded param must not be able to leave the piece silently off for
    the rest of the day; falling back to auto is always safe since its
    own validate() takes no speed-dependent params.
    """
    from boot_log import log

    recorded = last_mode.get_last_mode()
    if recorded is not None:
        try:
            kwargs = resolve_params(recorded["name"], recorded["params"])
            mode_runner.start(recorded["name"], REGISTRY[recorded["name"]], kwargs)
            log(f"scheduler: on — replaying last mode {recorded['name']}{kwargs}")
            return
        except (ValueError, KeyError) as exc:
            log(f"scheduler: on — last mode {recorded!r} failed to replay ({exc!r}), falling back to auto")

    mode_runner.start("auto", REGISTRY["auto"], {})
    log("scheduler: on — starting auto (no usable last mode recorded)")


def _turn_off(stop_event: threading.Event) -> None:
    """Home every unit, send it to 0° (a no-op after H, which already
    parks there — kept as explicit belt-and-braces), clear the queue, then
    power down. Mirrors the H/G staging pairing (H is staged; needs its
    own go_all()) used throughout server/patterns/helpers.py, and server/modes/auto.py's
    _play() homing phase.

    Terminal command is the immediate FFP, not the firmware's new staged
    FFD: D leaves needsHome clear, so a D-released unit parked at 0° stays
    under the firmware's position safeguard all night — a single flaky
    endstop read persisting past POS_CHECK_CONFIRM_MS would trigger a
    blocking re-home in the middle of the night (../hexagons/runner.cpp).
    P sets needsHome — unlike D, it's an explicit "stay put" that the
    firmware's position safeguard doesn't override (see
    ../hexagons/README.md) — the cost is a re-home next morning, which
    _turn_on()'s first move already pays for regardless.

    The wait before FFC/FFP is wrapped in try/finally, not gated on
    stop_event.wait()'s return value the way every mode's tick loop treats
    it (server/modes/wave.py:169,195 etc.) — bailing early here on a
    SIGTERM mid-sequence would leave every coil energised at an arbitrary
    angle for the rest of the night, which is strictly worse than taking
    an extra second to finish the sequence during shutdown.
    """
    from boot_log import log

    log("scheduler: off — homing, moving to 0°, and powering down")
    try:
        mode_runner.stop()  # stop thread + FFX + FFC
        settings.stage_speed()
        orchestrator.home_all()
        orchestrator.absolute(orchestrator.BROADCAST, 0)
        orchestrator.go_all()
    finally:
        wait_s = (HOME_SETTLE_MS * settings.speed_scale() + settle_ms()) / 1000
        stop_event.wait(wait_s)  # ignore the return value — see docstring
        orchestrator.clear_all()
        orchestrator.power_down_all()
