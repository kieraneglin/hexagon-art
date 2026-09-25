# server/store.py — Consolidated persisted-state store.
#
# Everything the orchestrator needs to remember across a restart or a
# redeploy lives in one JSON file (state.json under HEXAGON_FS_ROOT — see
# main.py), rather than one file per concern. A single Pi Zero W process
# with no concurrent writers doesn't need per-concern isolation; it does
# benefit from not needing a new module + .gitignore line + deploy.sh
# --exclude entry every time a new setting is added (see server/settings.py,
# and the off-hours schedule this is being built to support next).
#
# Format: {"version": 1,
#          "macros":    {"<name>": {"commands": "...", "group": ""}},
#          "settings":  {"default_rpm": 14.5},
#          "schedule":  {"enabled": false, "on_time": "09:00", "off_time": "22:00",
#                        "timezone": "America/Vancouver", "last_applied": null},
#          "last_mode": {"name": "auto", "params": {}}}
#
# server/macros.py, server/settings.py, server/schedule.py, and
# server/last_mode.py are thin typed facades over this module — none of
# them poke raw keys itself.

import json
import os
import threading

STATE_FILE = (os.getenv("HEXAGON_FS_ROOT") or "") + "/state.json"

# Legacy pre-consolidation macros file (server/macros.py used to own this
# directly). Never deleted by _migrate() below — kept on disk as a fallback
# in case state.json is ever lost, since it costs nothing to leave alone.
_LEGACY_MACROS_FILE = (os.getenv("HEXAGON_FS_ROOT") or "") + "/macros.json"

# Route handlers hold this across their whole load() -> mutate -> save()
# sequence. Under Waitress's thread pool, two concurrent writes (or a write
# racing a delete) would otherwise be a textbook lost-update race — the old
# single-threaded DIY server made this impossible by construction; a real
# WSGI server doesn't.
LOCK = threading.Lock()

_SKELETON = {"version": 1, "macros": {}, "settings": {}, "schedule": {}, "last_mode": {}}


def _migrate_macro_values(macros: dict) -> bool:
    """Upgrade plain-string macro values to the {commands, group} object
    format. Returns True if anything changed. Carried over verbatim from
    the pre-consolidation server/macros.py — real Pis have macros saved in
    both shapes."""
    changed = False
    for name, value in macros.items():
        if isinstance(value, str):
            macros[name] = {"commands": value, "group": ""}
            changed = True
    return changed


def _load_legacy_macros() -> dict:
    """Best-effort read of the pre-consolidation macros.json. Never raises."""
    try:
        with open(_LEGACY_MACROS_FILE) as f:
            data = json.load(f)
    except Exception:
        return {}
    macros = data.get("macros", {})
    _migrate_macro_values(macros)
    return macros


def load() -> dict:
    """Return the full state dict, migrating/seeding it as needed. Never
    raises — a missing or corrupt state.json falls back to legacy
    macros.json (if any) or a blank skeleton."""
    try:
        with open(STATE_FILE) as f:
            data = json.load(f)
    except Exception:
        data = {
            "version": 1,
            "macros": _load_legacy_macros(),
            "settings": {},
            "schedule": {},
            "last_mode": {},
        }
        save(data)
        return data

    changed = False
    if "macros" not in data:
        data["macros"] = {}
        changed = True
    if "settings" not in data:
        data["settings"] = {}
        changed = True
    if "schedule" not in data:
        data["schedule"] = {}
        changed = True
    if "last_mode" not in data:
        data["last_mode"] = {}
        changed = True
    if _migrate_macro_values(data["macros"]):
        changed = True

    if changed:
        save(data)
    return data


def save(data: dict) -> None:
    """Persist the state dict to flash, atomically.

    Writes to a temp file in the same directory, fsyncs it, then
    os.replace()s over the real path — an atomic rename on POSIX, so a
    reader (including this module's own load()) always sees either the
    whole old file or the whole new one, never a partial write. The
    previous macros-only implementation truncated-then-wrote in place,
    which a power cut mid-write could turn into a corrupt file that load()
    would silently discard — a bigger risk once the piece is power-cycled
    on a nightly schedule rather than only on a deploy.
    """
    tmp_path = STATE_FILE + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, STATE_FILE)
