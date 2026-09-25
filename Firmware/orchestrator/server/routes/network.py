"""
server/routes/network.py — on-Pi WiFi provisioning (Tools page)

Wraps deploy/hexagons-netcfg.py (deployed to
/usr/local/sbin/hexagons-netcfg — see AGENTS.md's "Network resilience" and
PI_SETUP.md §6) rather than shelling out to nmcli directly: creating a
NetworkManager profile needs root, and the app runs as an unprivileged
`pi` (deploy/hexagons.service). `scan`/`list` are read-only and need no
privilege, so those call the helper directly; `add`/`forget`/`reboot` go
through `sudo -n`, permitted only for these exact invocations by
/etc/sudoers.d/hexagons-netcfg (installed by `./deploy.sh --bootstrap` —
see deploy/pi-bootstrap.sh). This is the first place in this repo that
shells out to anything, so it sets the convention for the next one: a
short timeout, text=True, and the WiFi password never touches argv or a
log line — it's piped to the helper as JSON on stdin.

If the helper isn't installed — any non-Pi dev host (simhost/run.py on
macOS/Linux), or a Pi that hasn't had --bootstrap re-run since this
feature shipped — every endpoint degrades to {"available": false} rather
than a 500.
"""

import json
import subprocess

from flask import jsonify, request

HELPER = "/usr/local/sbin/hexagons-netcfg"
TIMEOUT_S = 20


class NetcfgUnavailable(RuntimeError):
    """The hexagons-netcfg helper isn't installed on this host."""


def _run(*args, stdin_data=None):
    """Run the helper, privileged only for the verbs sudoers allows.

    Returns the parsed JSON stdout on success. Raises NetcfgUnavailable if
    the helper binary itself is missing, or RuntimeError with a message
    fit to hand back to the client for any other failure.
    """
    privileged = args[0] in ("add", "forget", "reboot")
    cmd = ["sudo", "-n", HELPER, *args] if privileged else [HELPER, *args]
    try:
        result = subprocess.run(cmd, input=stdin_data, capture_output=True, text=True, timeout=TIMEOUT_S)
    except FileNotFoundError as exc:
        raise NetcfgUnavailable from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{args[0]} timed out") from exc

    try:
        payload = json.loads(result.stdout)
    except (ValueError, TypeError):
        raise RuntimeError((result.stderr or result.stdout or f"{args[0]} failed").strip() or f"{args[0]} failed")

    if isinstance(payload, dict) and payload.get("error"):
        raise RuntimeError(payload["error"])
    return payload


def get_network():
    """GET /api/network — scan results, saved hexagon-wifi-* profiles, and
    whether any of this is possible on this host at all.

    Response: {"available", "current", "saved": [...], "scan": [...]}
    `current` is the SSID wlan0 is actually on (from `saved`'s "active"
    flag), or null if it isn't one of ours (e.g. hexagon-ap).
    """
    try:
        scan = _run("scan")
        saved = _run("list")
    except NetcfgUnavailable:
        return jsonify({"available": False, "current": None, "saved": [], "scan": []})
    except RuntimeError as exc:
        return jsonify({"available": True, "current": None, "saved": [], "scan": [], "error": str(exc)})

    current = next((n["ssid"] for n in saved if n.get("active")), None)
    return jsonify({"available": True, "current": current, "saved": saved, "scan": scan})


def post_network():
    """POST /api/network — add (or overwrite) a WiFi profile, then reboot.

    Body: {"ssid": "...", "password": "...", "priority": 100}. `password`
    may be omitted/empty for an open network. Rebooting is deliberate, not
    incidental — hexagons-netwatch only re-runs its wait-then-decide
    WiFi/AP logic at boot (see AGENTS.md's "Network resilience"), so a
    newly-added profile can't actually be tried until the next one.

    Response: {"ok": true, "rebooting": true} or {"error": "..."}, 400.
    """
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        payload = {}

    ssid = (payload.get("ssid") or "").strip()
    if not ssid:
        return jsonify({"error": "ssid is required"}), 400

    password = payload.get("password") or ""
    if password and len(password) < 8:
        return jsonify({"error": "password must be at least 8 characters (or empty for an open network)"}), 400

    try:
        priority = int(payload.get("priority", 100))
    except (TypeError, ValueError):
        return jsonify({"error": "priority must be an integer"}), 400
    if not (0 <= priority <= 999):
        return jsonify({"error": "priority must be between 0 and 999"}), 400

    stdin_data = json.dumps({"ssid": ssid, "password": password, "priority": priority})
    try:
        _run("add", stdin_data=stdin_data)
        _run("reboot")
    except NetcfgUnavailable:
        return jsonify({"error": "network config unavailable on this host"}), 400
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify({"ok": True, "rebooting": True})


def delete_network(ssid):
    """DELETE /api/network/<ssid> — forget a saved profile. No reboot."""
    try:
        _run("forget", stdin_data=json.dumps({"ssid": ssid}))
    except NetcfgUnavailable:
        return jsonify({"error": "network config unavailable on this host"}), 400
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True})
