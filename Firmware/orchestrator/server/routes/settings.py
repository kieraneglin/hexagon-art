from flask import jsonify, request

from server import settings


def get_settings():
    """GET /api/settings — the persisted default motor speed, plus the
    range /admin/tools should let it be set within."""
    return jsonify(
        {
            "default_rpm": settings.default_rpm(),
            "min_rpm": settings.MIN_RPM,
            "max_rpm": settings.MAX_RPM,
        }
    )


def post_settings():
    """POST /api/settings — set the default motor speed.

    Body: {"default_rpm": 12.0}. Validates the range, persists it, then
    applies it to the bus immediately (settings.set_default_rpm() +
    apply_speed()) — a piece that's mid-mode picks up the new speed on its
    very next tick's FFG rather than waiting for the mode to be restarted.

    Response: {"ok": true, "default_rpm": 12.0}  on success
              {"error": "..."}                    on missing/bad value (400)
    """
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or "default_rpm" not in payload:
        return jsonify({"error": "default_rpm required"}), 400

    try:
        rpm = settings.set_default_rpm(payload["default_rpm"])
    except (TypeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400

    settings.apply_speed()
    return jsonify({"ok": True, "default_rpm": rpm})
