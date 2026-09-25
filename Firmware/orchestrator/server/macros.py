# server/macros.py — Macro persistence helpers
#
# A thin facade over server/store.py's consolidated state.json — macros
# used to be the only persisted state and owned a file of their own
# (macros.json), but that file is now folded into store.py's single
# state.json (see its module docstring). This module exists so
# server/routes/macros.py doesn't need to change shape: load()/save() here
# still deal in the bare {"macros": {...}} dict routes and src/api.js
# expect, just backed by the shared store underneath.
#
# Format: {"macros": {"name": {"commands": "01A0\r\n02A180\r\nFFG\r\n", "group": ""}}}

from server import store

# Re-exported so existing `with macro_store.LOCK:` call sites in
# server/routes/macros.py keep working unchanged.
LOCK = store.LOCK


def load():
    """Return {"macros": {...}} — the shape GET /api/macros and
    src/components/MacrosDrawer.jsx expect."""
    return {"macros": store.load()["macros"]}


def save(data):
    """Persist {"macros": {...}} back into the shared state store.

    Does NOT acquire LOCK itself — every call site in
    server/routes/macros.py already holds it across its whole
    load() -> mutate -> save() sequence (see LOCK above), and store.LOCK
    isn't reentrant.
    """
    state = store.load()
    state["macros"] = data.get("macros", {})
    store.save(state)
