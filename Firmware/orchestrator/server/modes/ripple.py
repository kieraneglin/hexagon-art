"""
server/modes/ripple.py — continuous concentric ripple from the centre.

The set-and-forget counterpart to server/patterns/water_drop.py: same
centre-outward (or edge-inward) radiating wave, but instead of a fixed
number of decaying cycles staged once and fired, this repeats indefinitely
until stopped, with the orchestrator itself re-triggering each drop rather
than relying on unit-side W timers to hold up over a long run (see
server/modes/runner.py's module docstring for why).

Motion model
------------
Every unit bobs a fixed ``amplitude`` up from ``start_angle`` and back, once
per ``period_ms``, at a fixed lag determined by its distance from the centre
unit (``helpers.radial_fractions``) — the same "how far from centre" measure
server/patterns/home_wave.py uses for its staggered homing wave, but smoothly
curving rather than home_wave's own per-unit lag being the only consumer
before this. No decay, no cycle count: it's a steady-state ripple, not a
decaying impulse.

``start_angle`` defaults to 0° (fully home) rather than MIDPOINT (90°,
mid-stroke) for the same reason server/modes/wave.py's does: depth follows
``15 * (1 - cos(angle))``, so linear velocity is at its maximum at 90° and
zero at 0°/180° — landing each bounce's stop at 0°/180° hides it inside the
mechanism's own natural deceleration rather than interrupting it at peak
speed. Bouncing is one-sided from ``start_angle`` (``helpers.stage_bounce``:
rise by ``amplitude``, return) rather than swinging through a baseline,
which is also why 0° works as a baseline at all.

Timing model
------------
A tick loop (``_TICK_S``) checks, each pass, whether any unit's next
scheduled bounce time has arrived; any that have are staged
(``helpers.stage_bounce``) and fired together with a single FFG — the same
tick-loop shape server/modes/wave.py and server/modes/shimmer.py use.

Unlike wave.py, a unit's lag here never changes between drops, so there's no
per-iteration recompute: every unit fires at every wall-clock time
``t ≡ offset[unit_id] (mod period_ms)``, and firing simply advances that
unit's next-due time by ``period_ms``. ``offset[unit_id]`` is
``fraction * spread_ms`` for ``direction=0`` (outward: centre fires first)
or ``(1 - fraction) * spread_ms`` for ``direction=1`` (inward: the outer
edge fires first, collapsing toward the centre) — see ``plan()``.

``spread_ms`` (how long the wavefront takes to cross centre-to-edge) and
``period_ms`` (how often a new drop starts, also each unit's own repeat
period) are independent knobs. When ``spread_ms > period_ms`` the modulo
schedule lets a new drop begin before the previous one has finished
travelling outward — several concentric rings in flight across the piece at
once, roughly ``spread_ms / period_ms`` of them — a "still water" look
rather than a single lone front. That's an intended use of the two knobs,
not an edge case to guard against.

Queue-safety guard
-------------------
A unit must finish one bounce before its next one is due, or staged
commands accumulate in its 64-slot queue without bound — invisible on real
hardware (see server/patterns/helpers.py's module docstring). ``validate()``
checks ``one_sided_bounce_ms(amplitude) <= period_ms`` up front (a unit's
repeat period is ``period_ms`` regardless of ``spread_ms`` or ``direction``),
plus that ``start_angle + amplitude <= 180`` (can't rise past full
extension), and raises rather than silently clamping either.

Parameters (all keyword arguments)
-----------------------------------
period_ms : int
    Milliseconds between successive drops — also the repeat period for any
    single unit. Floored by min_period_ms().
spread_ms : int
    Milliseconds the wavefront takes to travel from the centre unit to the
    farthest unit (or vice versa — see direction). See Timing model above
    for what spread_ms > period_ms does.
amplitude : int
    Degrees risen (from start_angle) per bounce. No decay.
start_angle : int
    Rest position each bounce starts and ends at, in degrees (default 0 —
    see Motion model for why). Must satisfy start_angle + amplitude <= 180.
direction : int
    0 = outward (centre fires first, ripple travels to the edge — default).
    1 = inward (edge fires first, ripple collapses toward the centre).
"""

import math
import time

import orchestrator
from server import settings
from server.modes import envelope
from server.patterns.helpers import one_sided_bounce_ms, radial_fractions, settle_ms, stage_bounce
from server.patterns.layout import ALL_UNITS

_TICK_S = 0.1

# Margin added on top of one_sided_bounce_ms(180) below — same
# rationale/value as server/modes/wave.py's _SWEEP_MARGIN_MS.
_PERIOD_MARGIN_MS = 50


def min_period_ms() -> int:
    """Floor for period_ms: the minimum time a full-amplitude (180°)
    one-sided bounce takes to complete at the currently configured speed
    (see helpers.one_sided_bounce_ms) plus margin — same shape as
    server/modes/wave.py's min_sweep_ms(). Was a fixed MIN_PERIOD_MS=4400
    constant before the default speed became configurable."""
    return math.ceil(one_sided_bounce_ms(180) + _PERIOD_MARGIN_MS)


def validate(
    period_ms: int = 6000,
    spread_ms: int = 6000,
    amplitude: int = 180,
    start_angle: int = 0,
    direction: int = 0,
) -> None:
    """Raise ValueError if these params would grow a unit's queue without
    bound, or ask a unit to rise past full extension. Called synchronously
    by the route before starting the mode thread, so a bad request 400s
    instead of silently overflowing or misbehaving.

    Accepts (and ignores) `direction` — it only ever changes the direction
    a fixed offset is measured in (see plan()), never the offset's range or
    a unit's repeat period, so it has no bearing on queue safety.
    """
    if start_angle + amplitude > 180:
        raise ValueError(
            f"start_angle={start_angle} + amplitude={amplitude} = {start_angle + amplitude}, "
            f"which is past full extension (180°). Lower one or both."
        )

    needed = one_sided_bounce_ms(amplitude)
    if needed > period_ms:
        raise ValueError(
            f"amplitude={amplitude} takes ~{needed:.0f}ms to bounce, but period_ms={period_ms} "
            f"is shorter — a unit wouldn't finish one drop before the next is due, growing its "
            f"queue without bound. Raise period_ms to at least {needed:.0f} or lower amplitude."
        )


def plan(spread_ms: int, direction: int) -> dict[int, float]:
    """Return unit_id -> this unit's offset in ms within one period, from
    its distance from centre. Pure function, kept separate from run() so
    the schedule itself can be exercised without a thread — mirrors
    server/modes/wave.py's plan(), except a unit's offset here never
    changes between periods, so run() calls this once rather than per
    iteration.
    """
    fractions = radial_fractions()
    if direction:
        return {unit_id: (1.0 - f) * spread_ms for unit_id, f in fractions.items()}
    return {unit_id: f * spread_ms for unit_id, f in fractions.items()}


def run(
    stop_event,
    period_ms: int = 6000,
    spread_ms: int = 6000,
    amplitude: int = 180,
    start_angle: int = 0,
    direction: int = 0,
) -> None:
    """Continuously repeating ripple; runs until stop_event is set.

    Usage (via server/modes/runner.py, not called directly):
        runner.start("ripple", ripple.run, {
            "period_ms": 6000,
            "spread_ms": 6000,
            "amplitude": 180,
            "start_angle": 0,
            "direction": 0
        })
    """
    validate(period_ms, spread_ms, amplitude, start_angle, direction)

    settings.stage_speed()
    orchestrator.absolute(orchestrator.BROADCAST, start_angle)
    orchestrator.go_all()
    if stop_event.wait(settle_ms() / 1000):
        return

    offset_ms = plan(spread_ms, direction)
    origin = time.monotonic()
    next_pass_ms = dict(offset_ms)

    while not stop_event.is_set():
        now_ms = (time.monotonic() - origin) * 1000
        fired = False
        for unit_id in ALL_UNITS:
            if now_ms >= next_pass_ms[unit_id]:
                stage_bounce(unit_id, envelope.scale(amplitude))
                next_pass_ms[unit_id] += period_ms
                fired = True
        if fired:
            orchestrator.go_all()

        if stop_event.wait(_TICK_S):
            break
