"""
server/modes/auto.py — "Auto" self-cycling installation mode.

The unattended-operation mode: cycles through server/modes/auto_config.py's
SEGMENTS forever, crossfading between them via server/modes/envelope.py
(silent -> full amplitude -> silent) rather than cutting motion abruptly at
each boundary. Registered as mode id "auto" (see server/modes/__init__.py)
with an empty MODE_META params list — auto_config.py, not the web UI, is
the single source of truth for what it does; see that file to retune it.

Why this needs no separate "armed" flag anywhere: server/modes/runner.py
already allows only one mode at a time, and every "take control back" path
in this codebase already goes through it — starting any other mode
(runner.start()), running a pattern (post_pattern's stop_thread()), and the
top bar's Stop button (post_stop -> runner.stop()) all stop whatever mode is
currently running. Registering auto as an ordinary mode means all of those
disarm it for free; re-enabling it is just starting the "auto" mode again,
same as any other.

Critical constraint: never call server.modes.runner.start() (or anything
that calls it) from inside run() below, or from a thread it spawns.
runner.start() calls runner.stop(), which joins the *currently running*
mode's thread — and this function *is* that thread once runner.start("auto",
run, {}) is called. Joining the current thread raises RuntimeError. Instead,
each segment's sub-mode is driven directly: its own run(child_stop_event,
**params) is called in a plain daemon thread this module owns, never through
REGISTRY/runner.
"""

import random
import threading

import orchestrator
from boot_log import log
from server import settings
from server.modes import auto_config as config
from server.modes import envelope, ripple, shimmer, wave
from server.patterns.helpers import settle_ms

_TICK_S = 0.1
_CHILD_JOIN_TIMEOUT_S = 3.0

# The only sub-modes that route their motion through helpers.stage_bounce —
# and so are the only ones envelope.py can fade (see its docstring). "life"
# stages absolute A moves from a cellular automaton instead and is
# deliberately excluded; validate() below rejects it by name if configured.
_SUBMODES = {"wave": wave.run, "ripple": ripple.run, "random": shimmer.run}
_VALIDATORS = {"wave": wave.validate, "ripple": ripple.validate, "random": shimmer.validate}

# Time-valued params across the three fade-aware sub-modes — scaled by
# settings.speed_scale() before being handed to a sub-mode's validate()/
# run() (see _scaled_params below). gen_ms (life's own time param) isn't
# here: life is excluded from auto entirely (see _SUBMODES above).
_TIME_PARAM_KEYS = {"sweep_ms", "period_ms", "spread_ms", "interval_ms"}


def _scaled_params(params: dict) -> dict:
    """Scale this segment's time-valued params by settings.speed_scale().

    auto_config.py's SEGMENTS are authored at settings.REFERENCE_RPM
    (14.5) — sweep_ms=5000/amplitude=180 has only ~13% headroom over
    one_sided_bounce_ms(180) at that speed. Run those same values
    unscaled at a slower configured RPM and a sub-mode's own validate()
    would either reject them outright or, worse, pass with the queue-
    overflow margin silently eaten away. Scaling keeps each segment's
    safety margin — and its choreography's visual shape — roughly
    constant at any configured speed instead of forcing auto_config.py to
    be authored defensively wide for the slowest speed anyone might ever
    configure.
    """
    scale = settings.speed_scale()
    if scale == 1.0:
        return dict(params)
    return {k: (int(round(v * scale)) if k in _TIME_PARAM_KEYS else v) for k, v in params.items()}


_lock = threading.Lock()
_current = {"segment": None, "index": None, "phase": None}


def _set_current(segment, index, phase):
    with _lock:
        _current["segment"] = segment
        _current["index"] = index
        _current["phase"] = phase


def current() -> dict:
    """Return {"segment": name|None, "index": int|None, "phase": str|None}
    — which sub-mode auto is actually driving right now, if any. `phase` is
    one of "homing" (every unit homing before this segment's sub-mode
    starts), "fade_in", "hold", "fade_out", or "gap" — see _play() below.
    Merged into GET /api/modes/status by server/routes/modes.py when auto
    is the mode reported running, since runner.status() only knows the
    top-level mode name ("auto"), not what auto is doing underneath."""
    with _lock:
        return dict(_current)


def validate() -> None:
    """Raise ValueError naming the offending server/modes/auto_config.py
    segment if SEGMENTS is unsafe to run. Called synchronously by
    POST /api/modes/auto before the background thread starts (see
    server/routes/modes.py) — same contract every other mode's validate()
    follows — so a bad edit to auto_config.py 400s at the button instead of
    failing inside a background thread nobody's watching.
    """
    segments = config.SEGMENTS
    if not segments:
        raise ValueError("auto_config.SEGMENTS is empty — nothing to run")

    for i, segment in enumerate(segments):
        mode = segment.get("mode")
        if mode not in _SUBMODES:
            raise ValueError(
                f"auto_config.SEGMENTS[{i}]: mode {mode!r} is not fade-aware "
                f"(must be one of {sorted(_SUBMODES)} — see auto_config.py's module docstring)"
            )

        minutes = segment.get("minutes")
        if not isinstance(minutes, (int, float)) or isinstance(minutes, bool) or minutes <= 0:
            raise ValueError(f"auto_config.SEGMENTS[{i}]: minutes must be a positive number, got {minutes!r}")

        params = segment.get("params", {})
        try:
            _VALIDATORS[mode](**_scaled_params(params))
        except ValueError as exc:
            raise ValueError(f"auto_config.SEGMENTS[{i}] ({mode}): {exc}") from exc
        except TypeError as exc:
            raise ValueError(f"auto_config.SEGMENTS[{i}] ({mode}): bad params {params!r} — {exc}") from exc


def _sequence(last_mode):
    """Return this cycle's segment order. Plain auto_config.SEGMENTS order
    unless config.SHUFFLE, in which case each cycle is freshly reshuffled —
    with the first two entries swapped if that shuffle would otherwise
    repeat `last_mode` (the previous cycle's final segment) immediately
    back-to-back."""
    segments = config.SEGMENTS
    if not config.SHUFFLE or len(segments) < 2:
        return list(segments)

    order = list(segments)
    random.shuffle(order)
    if order[0]["mode"] == last_mode:
        order[0], order[1] = order[1], order[0]
    return order


def _ramp(stop_event, start_gain, end_gain, duration_ms):
    """Step server/modes/envelope.py's gain from start_gain to end_gain over
    duration_ms, in _TICK_S slices, returning early (gain left wherever it
    last landed) the moment stop_event is set."""
    if duration_ms <= 0:
        envelope.set_gain(end_gain)
        return

    steps = max(1, round(duration_ms / (_TICK_S * 1000)))
    for i in range(1, steps + 1):
        if stop_event.wait(_TICK_S):
            return
        envelope.set_gain(start_gain + (end_gain - start_gain) * (i / steps))


def _play(stop_event, segment, index):
    """Run one segment end to end: home every unit, spawn its sub-mode,
    fade in, hold, fade out, tear the sub-mode down, then a beat of
    stillness (config.GAP_MS). Returns (normally or via stop_event) with
    the envelope back at 0 gain."""
    mode = segment["mode"]
    minutes = segment["minutes"]
    params = _scaled_params(segment.get("params", {}))

    envelope.set_gain(0.0)

    # Home every unit before this segment's sub-mode starts, so segments
    # never hand off to each other from wherever the previous one happened
    # to stop — accumulated position error would otherwise carry across a
    # rotation meant to run for days. Stage the speed first so the home
    # itself (and the sub-mode's opening broadcast right after) runs at the
    # configured speed, not whatever was last set — both ride this same
    # go_all(). H is staged, not immediate, so it needs its own go_all() to
    # fire (same pairing home_wave.py uses). Unlike home_wave's staggered
    # wave, this is a flat broadcast — simpler, at the cost of every unit's
    # endstop-search current landing in the same instant; see
    # auto_config.py's HOME_SETTLE_MS for the trade-off and the fallback if
    # that turns out to matter in practice.
    #
    # The wait after it is mandatory, not cosmetic: homing is blocking and
    # deaf (the firmware doesn't poll the UART mid-homeMotor()), so the
    # child sub-mode's opening broadcast below would be silently dropped
    # on every unit if it went out before homing has actually drained.
    # HOME_SETTLE_MS is sized against a full 360° search at
    # settings.REFERENCE_RPM (14.5) — scaled by speed_scale() so a slower
    # configured speed gets the correspondingly longer wait a real
    # endstop search takes at that speed.
    _set_current(mode, index, "homing")
    settings.stage_speed()
    orchestrator.home_all()
    orchestrator.go_all()
    if stop_event.wait(config.HOME_SETTLE_MS * settings.speed_scale() / 1000):
        return

    child_stop = threading.Event()
    child_errors = []

    def _run_child():
        try:
            _SUBMODES[mode](child_stop, **params)
        except Exception as exc:  # sub-mode crashing must not take auto down silently
            child_errors.append(exc)

    child = threading.Thread(target=_run_child, daemon=True)
    child.start()

    try:
        # Every sub-mode's run() opens with a broadcast A<start_angle> +
        # settle_ms() wait before it starts bouncing (wave.py/ripple.py/
        # shimmer.py) — wait that out here too so the fade-in is measured
        # from when motion actually begins, not from thread start.
        if stop_event.wait(settle_ms() / 1000):
            return

        _set_current(mode, index, "fade_in")
        _ramp(stop_event, 0.0, 1.0, config.FADE_IN_MS)
        if stop_event.is_set():
            return

        _set_current(mode, index, "hold")
        if stop_event.wait(minutes * 60):
            return

        _set_current(mode, index, "fade_out")
        _ramp(stop_event, 1.0, 0.0, config.FADE_OUT_MS)
    finally:
        child_stop.set()
        child.join(timeout=_CHILD_JOIN_TIMEOUT_S)
        if child.is_alive():
            # A sub-mode that won't stop promptly is the one failure mode
            # that would otherwise leave two command streams writing to the
            # bus at once. Cut the bus directly and give up on this run
            # rather than starting the next segment on top of it — stop()
            # was already promised within runner._JOIN_TIMEOUT_S.
            log(f"auto: sub-mode {mode!r} did not stop within {_CHILD_JOIN_TIMEOUT_S:.0f}s — halting")
            orchestrator.stop_all()
            orchestrator.clear_all()
            stop_event.set()
        if child_errors:
            log(f"auto: sub-mode {mode!r} raised {child_errors[0]!r}")
        envelope.set_gain(0.0)

    if stop_event.is_set():
        return
    _set_current(mode, index, "gap")
    stop_event.wait(config.GAP_MS / 1000)


def run(stop_event) -> None:
    """Cycle through auto_config.SEGMENTS forever, until stop_event is set.

    Usage (via server/modes/runner.py, not called directly):
        runner.start("auto", auto.run, {})
    """
    validate()

    try:
        last_mode = None
        while not stop_event.is_set():
            for index, segment in enumerate(_sequence(last_mode)):
                if stop_event.is_set():
                    break
                _play(stop_event, segment, index)
                last_mode = segment["mode"]
    finally:
        envelope.reset()
        _set_current(None, None, None)
