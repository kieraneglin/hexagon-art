"""
server/modes/runner.py — single-mode-at-a-time supervisor.

Patterns (server/patterns/) stage an entire choreography burst into unit
queues up-front and fire one FFG — the orchestrator never sleeps, and the
firmware's own W-command timing carries the rest. That's fine for anything
that finishes in seconds, but it relies on each unit's oscillator to keep
time, and those oscillators drift (OSCCAL is roughly 0.5-1% per LSB, and W
is a bare millis() deadline with no compensation — see
../hexagons/runner.cpp). Fine for a few seconds; not fine for something
meant to run for hours unattended.

Modes (server/modes/) invert that: the orchestrator itself is the clock.
A background thread wakes on a short, fixed tick, decides what (if
anything) needs to move *this* tick, stages just that, and fires FFG —
which re-syncs every unit's queue on every tick. Units only ever need to
keep time across one bounce (a few seconds), which they do fine; nothing
free-runs long enough to accumulate drift.

Only one mode may run at a time — starting a second stops the first
first, rather than interleaving two independent command streams on a bus
where orchestrator._send_lock only serializes single commands, not whole
bursts. A pattern run or the top bar's Stop button must also stop a
running mode (see server/routes/patterns.py) or its thread keeps writing
to the bus underneath them.
"""

import threading
import time

import orchestrator
from server import settings
from server.modes import envelope

_lock = threading.Lock()

# All fields below are guarded by _lock.
_thread: threading.Thread | None = None
_stop_event: threading.Event | None = None
_name: str | None = None
_params: dict = {}
_started_at: float | None = None

_JOIN_TIMEOUT_S = 2.0


def start(name: str, fn, params: dict) -> None:
    """Stop any running mode, apply the configured default speed, then
    start `fn(stop_event, **params)` as a daemon thread. `fn` must poll
    `stop_event` (e.g. via `stop_event.wait(seconds)`, never `time.sleep`)
    so stop() is prompt.

    settings.apply_speed() runs after stop() (which broadcasts FFX+FFC —
    a staged FFS before that would just be discarded) and before the
    thread starts, so this is the single place every mode launch (this
    function is the funnel — see server/routes/modes.py and main.py's
    boot auto-start) picks up whatever speed /admin/tools has configured.
    Individual modes also stage it again at the top of their own run()
    (see e.g. server/modes/wave.py) — needed for auto.py's sub-modes,
    which are spawned directly rather than through this function (see
    auto.py's module docstring), and harmless redundancy everywhere else.
    """
    stop()
    settings.apply_speed()

    stop_event = threading.Event()

    def _run():
        try:
            fn(stop_event, **params)
        finally:
            # Best-effort: if the mode function exits on its own (error or
            # natural completion) rather than via stop(), clear the running
            # state so status() doesn't report a dead thread as active.
            with _lock:
                global _thread, _stop_event, _name, _params, _started_at
                if _stop_event is stop_event:
                    _thread = None
                    _stop_event = None
                    _name = None
                    _params = {}
                    _started_at = None

    thread = threading.Thread(target=_run, daemon=True)

    with _lock:
        global _thread, _stop_event, _name, _params, _started_at
        _thread = thread
        _stop_event = stop_event
        _name = name
        _params = dict(params)
        _started_at = time.time()

    thread.start()


def stop_thread() -> bool:
    """Stop the running mode's background thread, if any — no bus traffic.

    Returns True if a mode was actually running. Split out from stop()
    below so a caller that's about to immediately overwrite every unit's
    queue anyway (post_pattern's FFN + pattern staging) doesn't also pay
    for a now-redundant FFX/FFC broadcast just to interrupt the thread.
    """
    with _lock:
        global _thread, _stop_event, _name, _params, _started_at
        thread, stop_event = _thread, _stop_event
        was_running = thread is not None
        _thread = None
        _stop_event = None
        _name = None
        _params = {}
        _started_at = None

    if stop_event is not None:
        stop_event.set()
    if thread is not None:
        thread.join(timeout=_JOIN_TIMEOUT_S)

    # Belt-and-braces: every stop path (mode stop, pattern run, top bar's
    # Stop, starting another mode) routes through here, so this guarantees
    # a stale fade gain from an interrupted auto mode (server/modes/auto.py)
    # can never linger and quietly shrink whatever starts next — even in
    # the pathological case where the thread above outlives its join.
    envelope.reset()

    return was_running


def stop() -> None:
    """Stop the running mode, if any, then broadcast FFX + FFC.

    Always sends FFX/FFC even when nothing was running — cheap, and lets
    callers (POST /api/modes/stop, the top bar's Stop button) use this as
    an unconditional "make sure nothing is moving" primitive. X alone
    already halts every motor and clears every queue; the trailing FFC is
    deliberate redundancy against a dropped command on a bus with no
    ACKs, not a second functional step.
    """
    stop_thread()
    orchestrator.stop_all()
    orchestrator.clear_all()


def status() -> dict:
    """Return {"running": name|None, "params": {...}, "started_at": ...}."""
    with _lock:
        return {"running": _name, "params": dict(_params), "started_at": _started_at}
