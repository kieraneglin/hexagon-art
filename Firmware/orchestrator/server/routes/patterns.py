from flask import jsonify, request

import orchestrator
from server import settings
from server.modes import runner as mode_runner
from server.patterns import PATTERN_META, REGISTRY

# Patterns that manage their own homing and shouldn't get an extra FFN
# prepended — currently just home_wave itself (see server/routes/patterns.py
# docstring below and server/patterns/home_wave.py).
_SKIP_HOME_PREFIX = {"home_wave"}


def get_patterns():
    """GET /api/patterns — return the list of available patterns with metadata."""
    return jsonify({"patterns": PATTERN_META})


def post_pattern(name):
    """POST /api/patterns/<name> — run a named pattern.

    Optional JSON body can pass keyword arguments to the pattern's run()
    function, e.g.:  {"cycles": 4, "ring_delay_ms": 300}

    Every pattern gets a staged speed broadcast (FFS<tenths>, the
    configured server/settings.py default_rpm) as slot 0 of every queue,
    ahead of anything pattern-specific — including home_wave, whose own
    homing wave should run at the configured speed same as everything
    else. Every pattern other than home_wave (which does its own homing)
    also gets a conditional home broadcast (FFN) right after, so a unit
    that was power_down()'d rejoins the choreography from a known datum
    instead of the wrong absolute angle. Both ride the pattern's own FFG —
    no extra orchestrator round-trip — and deliberately no wait for
    either to complete: S and a no-op N both drain instantly, and this is
    a last-ditch correction for the uncommon case (a unit that actually
    needed to home), not the primary path — it'll just start its first
    cycle a few seconds late rather than holding the whole piece still up
    front on every single run.

    Starting a pattern also stops any running mode's background thread
    (see server/modes/) — otherwise it would keep writing to the bus
    underneath this pattern's own bursts, since orchestrator._send_lock is
    per-command, not per-request. This uses stop_thread(), not stop(): no
    FFX/FFC broadcast, since this function's own FFN + pattern staging is
    about to overwrite every queue anyway.

    Response: {"ok": true, "pattern": "<name>"}  on success
              {"error": "..."}                    on unknown name
    """
    if name not in REGISTRY:
        return jsonify({"error": f"unknown pattern: {name}"}), 404

    mode_runner.stop_thread()

    # Parse optional kwargs from JSON body (best-effort; ignore missing or
    # malformed body — POST /api/patterns/stop-style callers send neither
    # a body nor a Content-Type at all).
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        payload = {}
    # Ensure we only pass plain value types — guard against injection.
    kwargs = {k: v for k, v in payload.items() if isinstance(v, (int, float, str, bool))}

    settings.stage_speed()
    if name not in _SKIP_HOME_PREFIX:
        orchestrator.conditional_home_all()

    REGISTRY[name](**kwargs)
    return jsonify({"ok": True, "pattern": name})


def post_stop():
    """POST /api/patterns/stop — emergency stop + clear all unit queues.

    This is what the top bar's Stop button hits. Delegates entirely to
    mode_runner.stop() — it stops any running mode's background thread
    first (without this, the thread would keep issuing commands after the
    FFX/FFC below, undoing the stop) and always sends FFX (stop all motors
    immediately, clearing every queue) followed by FFC (clear queues again
    — deliberate redundancy against a dropped command on a bus with no
    ACKs, not a functional second step), whether or not a mode was running.
    """
    mode_runner.stop()
    return jsonify({"ok": True})
