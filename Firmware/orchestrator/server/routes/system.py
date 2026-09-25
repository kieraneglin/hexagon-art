from flask import jsonify

import orchestrator
from server import schedule


def get_health():
    """Liveness + UART + clock check.

    On a one-way bus with no ACKs, "the HTTP server responds" and "the
    server is actually holding the serial port" are different facts —
    this endpoint reports both so a dialout-permission or wiring problem
    doesn't masquerade as a healthy deploy. clock_synced is the same idea
    applied to the daily on/off schedule (server/schedule.py): "the
    schedule is configured" and "the schedule is able to act" are
    different facts on a Pi Zero W with no RTC, whose clock may never
    sync at all on the hexagon-ap fallback.
    """
    port, ok, error = orchestrator.uart_status()
    return jsonify(
        {
            "ok": True,
            "uart": port,
            "uart_ok": ok,
            "uart_error": error,
            "clock_synced": schedule.clock_synced(),
        }
    )
