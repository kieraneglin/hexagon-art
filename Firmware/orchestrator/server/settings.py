# server/settings.py — Persisted orchestrator-wide settings.
#
# Currently just the default motor speed (S command), surfaced on
# /admin/tools. A thin typed accessor over server/store.py's "settings"
# sub-dict — callers never poke raw keys.
#
# Every unit runs at the ATtiny firmware's boot default of 14.5 RPM
# (../hexagons/hexagons.ino's setup()) until an S command says otherwise.
# That number isn't just a firmware default, though — it's baked into this
# codebase's own timing math (server/patterns/helpers.py's move_ms()), which
# every mode's queue-overflow guard is built on. default_rpm() is what
# move_ms() reads, so changing the configured speed here keeps every
# validator, MIN_*_MS floor, and settle wait correct at any speed — see
# helpers.py and server/modes/auto.py for the consumers.

import orchestrator
from server import store

# The firmware boot default, and the RPM this codebase's timing constants
# (server/patterns/helpers.py's move_ms, SETTLE_MS, and every mode's
# MIN_*_MS floor) were derived against. Also this setting's own default.
REFERENCE_RPM = 14.5
DEFAULT_RPM = 14.5

# ../hexagons/AGENTS.md lists 14.5f under "Do Not Change" — it's the
# resonance-minimising value for the 28BYJ-48 at this gear ratio, and
# because the ULN2003 drive is constant-voltage, slowing the motor burns
# *more* energy per move, not less (heat is proportional to time energised,
# and a slower move takes longer to cover the same angle). This range
# still lets the installation be tuned slower or faster without reaching
# speeds likely to skip steps under load (no position feedback exists to
# detect that on a one-way bus) or overheat badly.
MIN_RPM = 5.0
MAX_RPM = 20.0

# Cached so move_ms() — called many times per pattern/mode tick — doesn't
# hit disk on every call. A plain module global is fine here: the GIL makes
# a single reference read/write atomic, and staleness during the brief
# window of a concurrent set_default_rpm() is harmless (the next read picks
# up the new value).
_cached_rpm = None


def default_rpm() -> float:
    """Return the persisted default speed in RPM, loading it from disk on
    first access."""
    global _cached_rpm
    if _cached_rpm is None:
        _cached_rpm = store.load().get("settings", {}).get("default_rpm", DEFAULT_RPM)
    return _cached_rpm


def set_default_rpm(rpm: float) -> float:
    """Validate, persist, and cache a new default speed. Raises ValueError
    if out of range. Returns the stored value."""
    rpm = float(rpm)
    if not (MIN_RPM <= rpm <= MAX_RPM):
        raise ValueError(f"default_rpm must be between {MIN_RPM} and {MAX_RPM}, got {rpm}")

    with store.LOCK:
        state = store.load()
        state.setdefault("settings", {})["default_rpm"] = rpm
        store.save(state)

    global _cached_rpm
    _cached_rpm = rpm
    return rpm


def speed_scale() -> float:
    """REFERENCE_RPM / default_rpm() — how much slower (>1) or faster (<1)
    than this codebase's tuned baseline the configured speed is. Used by
    server/modes/auto.py to scale each segment's time-valued params
    (sweep_ms, period_ms, ...) so choreography keeps its shape — and its
    queue-safety margin — at any configured speed."""
    return REFERENCE_RPM / default_rpm()


def stage_speed() -> None:
    """Stage a broadcast speed change to the configured default_rpm. S is
    a staged command (../hexagons/runner.cpp's SET_SPEED case) — needs its
    own go_all()/an already-planned one to take effect."""
    orchestrator.speed(orchestrator.BROADCAST, default_rpm())


def apply_speed() -> None:
    """stage_speed() + an immediate go_all() — for callers that aren't
    about to fire a go_all() of their own right after (e.g.
    server/modes/runner.py's start(), which stops the previous mode first
    and so has no other broadcast pending)."""
    stage_speed()
    orchestrator.go_all()
