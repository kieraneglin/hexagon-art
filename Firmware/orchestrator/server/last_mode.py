# server/last_mode.py — the last mode a human explicitly started, for
# server/scheduler.py to replay at the daily "on" transition.
#
# Deliberately its own tiny facade rather than living inside
# server/schedule.py or server/settings.py: server/modes/runner.py already
# imports server/settings.py (for apply_speed()), and the one call site that
# records here (server/routes/modes.py's post_mode()) has no reason to know
# schedule.py exists. Keeping this decoupled means neither module gains an
# import it doesn't otherwise need.
#
# Recorded only at the POST /api/modes/<name> funnel — not inside
# server/modes/runner.py's start() itself. That distinction matters:
# runner.start() is also called by main.py's boot auto-arm and by
# server/scheduler.py's own "on" replay, and if either of those recorded,
# the very next boot (or the very next scheduled "on") would overwrite
# whatever a visitor last chose with "auto", making "replay the last-used
# mode" degenerate into "always auto". Routing the write through the HTTP
# funnel means only an explicit human action — the calm UI's mode buttons,
# the admin Modes page, or a raw POST — ever updates it.

from server import store

_cached = None  # {"name": str, "params": dict} | None — None means "never recorded"


def get_last_mode() -> dict | None:
    """Return the last explicitly-started {"name", "params"}, or None if
    nothing has been recorded yet (fresh install, or state.json predates
    this feature)."""
    global _cached
    if _cached is None:
        stored = store.load().get("last_mode") or {}
        if "name" in stored:
            _cached = {"name": stored["name"], "params": dict(stored.get("params", {}))}
    return dict(_cached) if _cached else None


def set_last_mode(name: str, params: dict) -> None:
    """Persist the most recently started mode. Compare-before-write: a
    visitor mashing the same mode button repeatedly (or a mode with no
    params, like "auto") shouldn't cost a flash write every time."""
    global _cached
    candidate = {"name": name, "params": dict(params)}
    if _cached == candidate:
        return

    with store.LOCK:
        state = store.load()
        state["last_mode"] = candidate
        store.save(state)

    _cached = candidate
