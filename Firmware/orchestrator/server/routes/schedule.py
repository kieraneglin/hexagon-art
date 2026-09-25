"""
server/routes/schedule.py — GET/POST /api/schedule (calm UI's Settings page).

A separate route rather than folding into /api/settings: post_settings()
(server/routes/settings.py) hard-requires "default_rpm" in the body, so it
can't accept a schedule-shaped partial patch without being reworked, and
the two settings groups (motor speed vs. daily schedule) have nothing in
common besides both living under server/store.py's "settings"-adjacent
keys.
"""

import datetime

from flask import jsonify, request

from server import schedule
from server import scheduler as mode_scheduler


def get_schedule():
    """GET /api/schedule — the persisted schedule plus everything the
    settings page needs to render without a second request: the Pi's
    current local time (the only way a visitor with no SSH access can
    tell the clock is wrong), whether that clock is trustworthy, what the
    scheduler is actually doing right now, and the next on/off boundary.
    """
    cfg = schedule.get()
    synced = schedule.clock_synced()

    now_local = None
    next_transition = None
    if synced:
        now = datetime.datetime.now(schedule.tz())
        now_local = now.isoformat()
        next_transition = schedule.next_transition(now)

    return jsonify(
        {
            **cfg,
            "clock_synced": synced,
            "now_local": now_local,
            "next_transition": next_transition,
            "scheduler": mode_scheduler.status(),
        }
    )


def post_schedule():
    """POST /api/schedule — partial patch, e.g. {"enabled": true} or
    {"on_time": "08:30"}. Validates the *merged* result (a patch touching
    only on_time still has to be checked against the stored off_time),
    persists it, and returns the new stored value.

    Response: {"ok": true, ...merged schedule...}  on success
              {"error": "..."}                       on 400
    """
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "expected a JSON object body"}), 400

    try:
        saved = schedule.update(**payload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify({"ok": True, **saved})
