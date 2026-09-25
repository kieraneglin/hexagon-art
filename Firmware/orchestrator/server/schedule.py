# server/schedule.py — Persisted daily on/off schedule + timezone.
#
# A thin typed accessor over server/store.py's "schedule" sub-dict, same
# shape as server/settings.py: module constants for defaults, a cached
# getter, a validating setter that does the load/mutate/save cycle under
# store.LOCK. Surfaced on the calm UI's /settings page (see
# src/pages/SettingsPage.jsx) — the first visitor-facing settings surface
# this codebase has, as opposed to /admin/tools's operator-facing ones.
#
# Two Pi-specific facts this module has to account for:
#
#   - The Pi Zero W has no RTC. Its clock comes entirely from
#     systemd-timesyncd over the network, which may never succeed at all on
#     the hexagon-ap fallback (no internet — see PI_SETUP.md).
#     clock_synced() is what lets server/scheduler.py refuse
#     to enforce a schedule against a clock it can't trust — "fail on": an
#     art piece that stays running when it should have slept is a far
#     better failure than one that stays dark all day for an undiagnosable
#     reason.
#   - British Columbia's move to permanent DST means whatever tzdata the OS
#     image shipped with can go stale under us — see
#     deploy/hexagons-tzdata.timer for the periodic refresh this assumes.

from datetime import datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from server import store

DEFAULT_TIMEZONE = "America/Vancouver"
DEFAULT_ON = "09:00"
DEFAULT_OFF = "22:00"
DEFAULT_ENABLED = False  # opt-in — today's always-on behaviour is the default

_cached = None  # the full schedule dict, or None (not yet loaded)

# Once a synced clock has been observed, trust it for the rest of the
# process's life — see clock_synced()'s docstring for why flip-flopping
# would be actively harmful, not just noisy.
_synced_latched = False


def _defaults() -> dict:
    return {
        "enabled": DEFAULT_ENABLED,
        "on_time": DEFAULT_ON,
        "off_time": DEFAULT_OFF,
        "timezone": DEFAULT_TIMEZONE,
        "last_applied": None,  # "on" | "off" | None — see set_last_applied()
    }


def get() -> dict:
    """Return the persisted schedule, defaults filled in for any key never
    saved (fresh install, or a state.json that predates a given key)."""
    global _cached
    if _cached is None:
        stored = store.load().get("schedule", {})
        _cached = {**_defaults(), **stored}
    return dict(_cached)


def _parse_hhmm(value: str, label: str) -> time:
    try:
        return datetime.strptime(value, "%H:%M").time()
    except (TypeError, ValueError):
        raise ValueError(f'{label} must be 24-hour "HH:MM", got {value!r}') from None


def _validate(candidate: dict) -> None:
    """Raise ValueError if `candidate` (a full schedule dict, post-merge)
    isn't internally consistent. Validates the *merged* result, not just
    whatever a partial PATCH touched — a request that only changes on_time
    still has to be checked against the stored off_time."""
    on_t = _parse_hhmm(candidate["on_time"], "on_time")
    off_t = _parse_hhmm(candidate["off_time"], "off_time")
    if on_t == off_t:
        raise ValueError("on_time and off_time must differ")

    try:
        ZoneInfo(candidate["timezone"])
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"unknown timezone: {candidate['timezone']!r}") from None

    if not isinstance(candidate["enabled"], bool):
        raise ValueError("enabled must be a boolean")


def update(**patch) -> dict:
    """Validate `patch` merged onto the current schedule, persist it, and
    return the new stored value. Raises ValueError on any problem — an
    unknown key, a malformed time, an unknown timezone, or on_time ==
    off_time.

    Unknown keys are rejected rather than silently accepted: the calm UI is
    the one surface in this codebase with no operator double-checking a
    raw command afterward, so a typo'd field should 400 instead of being
    quietly ignored.
    """
    current = get()
    unknown = set(patch) - set(_defaults())
    if unknown:
        raise ValueError(f"unknown schedule field(s): {sorted(unknown)}")

    candidate = {**current, **patch}
    _validate(candidate)

    with store.LOCK:
        state = store.load()
        state["schedule"] = candidate
        store.save(state)

    global _cached
    tz_changed = patch.get("timezone") not in (None, current["timezone"])
    _cached = candidate
    if tz_changed:
        # A fresh ZoneInfo(name) call picks up whatever tzdata is on disk
        # right now — clear the interpreter-wide cache so an *existing*
        # reference to the old (or a differently-named-but-same) zone can't
        # keep serving stale rules for the life of the process.
        ZoneInfo.clear_cache()

    return dict(candidate)


def set_last_applied(state: str | None) -> None:
    """Record which desired state ("on"/"off"/None) the scheduler last
    actually drove the hardware to. Read back on the next process start so
    a restart during an off-period (e.g. a routine ./deploy.sh at 11pm)
    doesn't replay the whole home+power-down sequence — see
    server/scheduler.py's initial-edge handling."""
    current = get()
    if current["last_applied"] == state:
        return
    with store.LOCK:
        stored_state = store.load()
        sched = {**_defaults(), **stored_state.get("schedule", {})}
        sched["last_applied"] = state
        stored_state["schedule"] = sched
        store.save(stored_state)

    global _cached
    _cached = sched


def tz() -> ZoneInfo:
    """The configured timezone. Constructing fresh each call is cheap —
    the zoneinfo module keeps its own internal cache — and means a
    ZoneInfo.clear_cache() from update() above is picked up immediately."""
    return ZoneInfo(get()["timezone"])


def is_on_at(now: datetime) -> bool:
    """Should the piece be "on" at `now` (a tz-aware datetime)?

    Compares wall-clock *time-of-day components* against the configured
    on_time/off_time — never constructs a local datetime for the
    on/off boundary itself. That's deliberate, not incidental: on a DST
    transition, 02:30 either doesn't exist (spring forward) or happens
    twice (fall back), and building `datetime(year, month, day, 2, 30,
    tzinfo=tz)` would have to reason about `fold`. Comparing bare `time`
    values sidesteps the question entirely — there's always a well-defined
    "current time of day" to compare against, even on the transition day
    itself. Combined with server/scheduler.py's edge-triggering (it acts on
    a *change* in this function's return value, not on a fixed instant), a
    skipped or repeated wall-clock hour is harmless: whichever tick first
    observes the new time-of-day fires the transition, a tick late at
    worst — a fire-at-the-exact-instant scheduler would silently miss the
    day entirely instead.

    `on_time < off_time` is a same-day window (e.g. 09:00-22:00);
    `on_time > off_time` wraps past midnight (e.g. 20:00-02:00). The window
    is on_time-inclusive, off_time-exclusive either way, so on_time and
    off_time can never both read "on" at once.
    """
    cfg = get()
    on_t = _parse_hhmm(cfg["on_time"], "on_time")
    off_t = _parse_hhmm(cfg["off_time"], "off_time")
    now_t = now.timetz().replace(tzinfo=None)

    if on_t < off_t:
        return on_t <= now_t < off_t
    return now_t >= on_t or now_t < off_t  # overnight wrap


def clock_synced() -> bool:
    """Whether the OS clock is currently trustworthy — systemd-timesyncd
    drops this marker file the moment it completes its first successful
    sync, and never removes it again short of an unclean shutdown.

    Latched: once observed True, stays True for the rest of the process's
    life, even if a later check somehow reads False again (e.g.
    timesyncd restarting and the file briefly disappearing). Without the
    latch, a schedule that's been enforcing correctly for hours could
    spuriously "lose" the clock for one 30s tick, reset its edge memory,
    and refire a full shutdown sequence in the middle of the afternoon —
    worse than just trusting the sync it already proved it has.

    HEXAGON_CLOCK_SYNCED=1/0 overrides the file check entirely — the
    marker path is systemd-specific and never exists on a dev machine
    (macOS, or a container with no timesyncd), so this is how
    simhost/run.py and manual testing exercise the enforced path at all.
    """
    global _synced_latched
    if _synced_latched:
        return True

    import os

    override = os.getenv("HEXAGON_CLOCK_SYNCED")
    if override is not None:
        synced = override == "1"
    else:
        synced = os.path.exists("/run/systemd/timesync/synchronized")

    if synced:
        _synced_latched = True
    return synced


def next_transition(now: datetime) -> dict | None:
    """The next on/off boundary at-or-after `now`, as
    {"at": datetime, "state": "on"|"off"}, or None if disabled. Purely
    informational (GET /api/schedule, for the settings page) — the
    scheduler itself never looks at this, only at is_on_at()'s current
    verdict each tick.
    """
    cfg = get()
    if not cfg["enabled"]:
        return None

    on_t = _parse_hhmm(cfg["on_time"], "on_time")
    off_t = _parse_hhmm(cfg["off_time"], "off_time")

    from datetime import timedelta

    candidates = []
    for boundary_t, state in ((on_t, "on"), (off_t, "off")):
        candidate = now.replace(hour=boundary_t.hour, minute=boundary_t.minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        candidates.append((candidate, state))

    candidates.sort(key=lambda pair: pair[0])
    at, state = candidates[0]
    return {"at": at.isoformat(), "state": state}
