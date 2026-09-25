"""
server/modes/shimmer.py — "Random" independent per-unit shimmer.

Registered under mode id "random" (see server/modes/__init__.py) — the file
itself is named shimmer.py rather than random.py to avoid shadowing the
stdlib module for no benefit; nothing here needs `import random` (see
Randomness below).

Unlike wave.py, there's no coordination between units at all: each unit
independently rolls its own chance to bounce in-and-out by its own random
amount, on its own schedule. The result reads as ambient shimmer rather
than a directional effect.

Randomness
----------
Uses helpers.rand_stream (the same LCG server/patterns/sparkle.py and
rain.py use), even though its original CircuitPython-parity rationale
doesn't apply here: this mode never runs on unit firmware, only in the
orchestrator process, so there's no "preview in the simulator, replay
identically on the wall" concern to preserve. Reused anyway to keep one
randomness idiom in the codebase rather than introducing a second
(stdlib random) alongside it. Note that unlike a pattern's fully
deterministic staged burst, a given seed does NOT reproduce byte-identical
output run to run: which units are due to roll on a given tick depends on
real wall-clock timing, which varies. The seed controls each unit's
initial offset and the sequence of "does it fire / how big" draws, not
the exact wall-clock moments those draws happen.

Timing model
------------
Same tick-loop shape as wave.py's, but per-unit schedules are independent
and randomly offset rather than sharing one phase:

  - Each unit gets an initial roll-offset in [0, interval_ms) so units
    don't all roll in lockstep at t=0, t=interval, t=2*interval — that
    would read as mechanical pulsing, not organic shimmer.
  - Every tick (_TICK_S), any unit whose next roll time has arrived draws
    two numbers: whether it fires this roll (compared against `density`)
    and, if so, how big (uniform between min_amplitude and max_amplitude).
    Its next roll time then advances by interval_ms regardless of whether
    it fired.
  - A firing unit is staged via helpers.stage_bounce — M+a, M-a from
    start_angle: rise, then return, exactly "in and out by a random
    amount." One FFG per tick covers whatever fired that tick.

``start_angle`` defaults to 0° for the same reason wave.py's does (see that
module's Motion model docstring) — landing each bounce's stop at 0°/180°
instead of mid-stroke hides it inside the mechanism's own deceleration.
stage_bounce's one-sided rise-and-return shape is what makes 0° viable at
all: helpers.stage_cycles' symmetric swing needs headroom on both sides of
its baseline, so it can't sit at 0° without going negative.

Queue-safety guard
-------------------
Reuses the same invariant wave.py relies on: a unit is only rolled again
interval_ms after its previous roll, so as long as
interval_ms >= helpers.one_sided_bounce_ms(max_amplitude), a unit's bounce
always finishes before it could possibly be re-triggered — no
busy-tracking needed, no unbounded queue growth. validate() checks this up
front (plus that min_amplitude <= max_amplitude and
start_angle + max_amplitude <= 180) so a bad request 400s instead of
silently overflowing a unit's 64-slot queue or rising past full extension.

Parameters (all keyword arguments)
-----------------------------------
seed : int
    PRNG seed — controls per-unit offsets and fire/size draws (see
    Randomness above; does not make wall-clock output reproducible).
interval_ms : int
    Roughly how often each unit gets a chance to bounce. Floored so a
    unit's previous bounce always finishes first — see Queue-safety guard.
density : float
    Probability (0-1) a unit fires on any given roll.
min_amplitude : int
    Lower bound (degrees) of a firing unit's random bounce size.
max_amplitude : int
    Upper bound (degrees) of a firing unit's random bounce size.
start_angle : int
    Rest position each bounce starts and ends at, in degrees (default 0 —
    see Timing model for why). Must satisfy start_angle + max_amplitude <= 180.
"""

import math
import time

import orchestrator
from server import settings
from server.modes import envelope
from server.patterns.helpers import one_sided_bounce_ms, rand_stream, settle_ms, stage_bounce
from server.patterns.layout import ALL_UNITS

_TICK_S = 0.1

# Margin added on top of one_sided_bounce_ms(180) below — same
# rationale/value as server/modes/wave.py's _SWEEP_MARGIN_MS.
_INTERVAL_MARGIN_MS = 50


def min_interval_ms() -> int:
    """Floor for interval_ms: the minimum time a full-amplitude (180°)
    one-sided bounce takes to complete at the currently configured speed
    (see helpers.one_sided_bounce_ms) plus margin — max_amplitude's own
    slider max is 180, so this guarantees validate() never rejects a
    combination where every field happens to be maxed independently (see
    simhost/selftest.py's "extreme" params check). Was a fixed
    MIN_INTERVAL_MS=4400 constant before the default speed became
    configurable."""
    return math.ceil(one_sided_bounce_ms(180) + _INTERVAL_MARGIN_MS)


def validate(
    seed: int = 1,
    interval_ms: int = 5000,
    density: float = 0.35,
    min_amplitude: int = 30,
    max_amplitude: int = 120,
    start_angle: int = 0,
) -> None:
    """Raise ValueError if these params would grow a unit's queue without
    bound, if the amplitude range is inverted, or if a unit could be asked
    to rise past full extension. Called synchronously by the route before
    starting the mode thread, so a bad request 400s instead of silently
    overflowing or misbehaving.

    Accepts (and ignores) `seed` even though it's not itself safety-relevant
    — callers (POST /api/modes/<name>, simhost/selftest.py) call this with
    the mode's full merged kwargs dict, same as run() gets, not a
    validate()-specific subset.
    """
    if min_amplitude > max_amplitude:
        raise ValueError(f"min_amplitude={min_amplitude} is greater than max_amplitude={max_amplitude}")

    if start_angle + max_amplitude > 180:
        raise ValueError(
            f"start_angle={start_angle} + max_amplitude={max_amplitude} = {start_angle + max_amplitude}, "
            f"which is past full extension (180°). Lower one or both."
        )

    needed = one_sided_bounce_ms(max_amplitude)
    if needed > interval_ms:
        raise ValueError(
            f"max_amplitude={max_amplitude} takes ~{needed:.0f}ms to bounce, but interval_ms={interval_ms} "
            f"is shorter — a unit could be re-rolled before finishing its previous bounce, growing its "
            f"queue without bound. Raise interval_ms to at least {needed:.0f} or lower max_amplitude."
        )


def run(
    stop_event,
    seed: int = 1,
    interval_ms: int = 5000,
    density: float = 0.35,
    min_amplitude: int = 30,
    max_amplitude: int = 120,
    start_angle: int = 0,
) -> None:
    """Continuous independent per-unit shimmer; runs until stop_event is set.

    Usage (via server/modes/runner.py, not called directly):
        runner.start("random", shimmer.run, {"interval_ms": 5000, "density": 0.35, ...})
    """
    validate(
        seed=seed,
        interval_ms=interval_ms,
        density=density,
        min_amplitude=min_amplitude,
        max_amplitude=max_amplitude,
        start_angle=start_angle,
    )

    settings.stage_speed()
    orchestrator.absolute(orchestrator.BROADCAST, start_angle)
    orchestrator.go_all()
    if stop_event.wait(settle_ms() / 1000):
        return

    rand = rand_stream(seed)
    origin = time.monotonic()
    # Stagger each unit's first roll so they don't all check in lockstep.
    next_roll_ms = {unit_id: rand() * interval_ms for unit_id in ALL_UNITS}

    while not stop_event.is_set():
        now_ms = (time.monotonic() - origin) * 1000
        fired = False
        for unit_id in ALL_UNITS:
            if now_ms >= next_roll_ms[unit_id]:
                next_roll_ms[unit_id] += interval_ms
                if rand() <= density:
                    amp = int(round(min_amplitude + rand() * (max_amplitude - min_amplitude)))
                    stage_bounce(unit_id, envelope.scale(amp))
                    fired = True
        if fired:
            orchestrator.go_all()

        if stop_event.wait(_TICK_S):
            break
