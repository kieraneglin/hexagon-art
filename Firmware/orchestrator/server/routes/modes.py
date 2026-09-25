from flask import jsonify, request

from server import last_mode
from server.modes import REGISTRY, mode_meta, resolve_params
from server.modes import auto as auto_mode
from server.modes import runner as mode_runner


def get_modes():
    """GET /api/modes — return the list of available modes with metadata.

    mode_meta() (not the static MODE_META) so the four speed-dependent
    slider minimums reflect whatever default speed is currently configured
    (see server/modes/__init__.py, server/settings.py).
    """
    return jsonify({"modes": mode_meta()})


def get_mode_status():
    """GET /api/modes/status — which mode (if any) is currently running.

    When "auto" is the running mode, runner.status() alone only reports the
    top-level name — auto.current() adds which sub-mode it's actually
    driving right now (see server/modes/auto.py), under "detail", so the
    Modes page can show e.g. "Running · wave" instead of a bare "Running".
    """
    status = mode_runner.status()
    if status["running"] == "auto":
        status["detail"] = auto_mode.current()
    return jsonify(status)


def post_mode(name):
    """POST /api/modes/<name> — start a continuously-running mode.

    Unlike POST /api/patterns/<name>, which fires a one-off burst and
    returns immediately, this starts a background thread (see
    server/modes/runner.py) that keeps issuing commands until
    POST /api/modes/stop, a pattern run, or the top bar's Stop button stops
    it. Starting a mode also stops any mode already running — only one may
    run at a time.

    Optional JSON body can pass keyword arguments, e.g. {"sweep_ms": 8000}.
    Unlike POST /api/patterns/<name>, unknown keys and out-of-range values
    are rejected with 400 rather than raising a 500 or silently starting a
    mode that will overflow a unit's queue. The coercion/validation itself
    lives in server/modes/__init__.py's resolve_params() — shared with
    server/scheduler.py's replay of the last-used mode at the daily "on"
    transition, so both paths enforce the identical queue-safety guard.

    Recording to server/last_mode.py happens here, at this HTTP funnel,
    deliberately not inside runner.start() itself — see last_mode.py's
    module docstring for why that distinction is what keeps "replay the
    last-used mode" from degenerating into "always auto".

    Response: {"ok": true, "mode": "<name>"}  on success
              {"error": "..."}                 on unknown name / bad params
    """
    if name not in REGISTRY:
        return jsonify({"error": f"unknown mode: {name}"}), 404

    payload = request.get_json(silent=True)
    try:
        kwargs = resolve_params(name, payload if isinstance(payload, dict) else {})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    mode_runner.start(name, REGISTRY[name], kwargs)
    last_mode.set_last_mode(name, kwargs)
    return jsonify({"ok": True, "mode": name})


def post_mode_stop():
    """POST /api/modes/stop — stop the running mode (if any) + FFX/FFC."""
    mode_runner.stop()
    return jsonify({"ok": True})
