"""
server/modes/wave.py — continuous edge-to-edge travelling wave.

The set-and-forget counterpart to server/patterns/sweep.py: same directional
projection, but instead of a fixed number of decaying cycles staged once and
fired, this repeats indefinitely until stopped, with the orchestrator itself
re-triggering each pass rather than relying on unit-side W timers to hold up
over a long run (see server/modes/runner.py's module docstring for why).

Motion model
------------
Every unit bobs a fixed ``amplitude`` up from ``start_angle`` and back, once
per ``sweep_ms``, at a fixed phase offset determined by its position along
``angle_deg`` — the same projection sweep.py uses
(``helpers.projection_fractions``). No decay, no cycle count: it's a
steady-state travelling wave, not a decaying impulse.

``start_angle`` defaults to 0° (fully home) rather than MIDPOINT (90°,
mid-stroke) deliberately: depth follows ``15 * (1 - cos(angle))``, so linear
velocity (``d(depth)/dtheta = 15*sin(theta)``) is at its *maximum* at 90°
and *zero* at 0°/180°. Each bounce still ends in a genuine stop (queued
commands don't carry momentum across separate firings — see Timing model),
but stopping at 0°/180° lands that stop where the mechanism is already
naturally decelerating toward zero velocity, rather than interrupting it at
peak speed. Same discrete stop at the command level, much less visually
jarring. Bouncing is one-sided from ``start_angle`` (``helpers.stage_bounce``:
rise by ``amplitude``, return) rather than swinging through a baseline,
which is also why 0° works as a baseline at all — a symmetric swing through
0° would require going negative.

Timing model
------------
A tick loop (``_TICK_S``) checks, each pass, whether any unit's next
scheduled bounce time has arrived; any that have are staged
(``helpers.stage_bounce``) and fired together with a single FFG. Passes are
grouped into iterations of ``sweep_ms`` each, counted from a fixed
``time.monotonic()`` origin (iteration boundaries are ``iteration *
sweep_ms``, not accumulated by re-adding an interval each tick, so
scheduling doesn't drift even though the tick loop itself has jitter). Every
unit's phase offset within an iteration is recomputed at that iteration's
start from ``projection_fractions`` — a no-op recompute (same numbers) when
``angle_step_deg`` is 0, and how a nonzero ``angle_step_deg`` takes effect
(see Rotation below).

Rotation
--------
``angle_step_deg`` adds ``iteration * angle_step_deg`` to ``angle_deg`` each
iteration (iteration 0 = the first full sweep, at ``angle_deg`` exactly),
wrapping mod 360 — so the sweep direction itself slowly rotates across
iterations rather than repeating the same heading forever, like a very slow
radar sweep instead of a fixed travelling wave. 0 (default) reproduces the
original fixed-direction behaviour exactly. Every unit's phase offset within
an iteration is guaranteed strictly less than that iteration's own
``sweep_ms`` window (``projection_fractions`` returns values in [0, 1)), so
a unit is always scheduled to fire before the next iteration's recompute
overwrites its slot — direction never skips a unit's turn regardless of
``angle_step_deg``.

Queue-safety guard
-------------------
A unit must finish one bounce before its next one is due, or staged
commands accumulate in its 64-slot queue without bound — invisible on real
hardware (see server/patterns/helpers.py's module docstring). ``validate()``
checks ``one_sided_bounce_ms(amplitude) <= sweep_ms`` up front (the
one-sided rise-then-return shape is two legs, not three — see
one_sided_bounce_ms), plus that ``start_angle + amplitude <= 180`` (can't
rise past full extension), and raises rather than silently clamping either.

Parameters (all keyword arguments)
-----------------------------------
angle_deg : int
    Sweep heading in degrees (0-330, step 30), same convention as sweep.py:
    0 = W->E, 90 = N->S, 60/120 = corner to corner.
sweep_ms : int
    Milliseconds for the wave to cross the piece — also the repeat period
    for any single unit. Floored by min_sweep_ms().
amplitude : int
    Degrees risen (from start_angle) per bounce.
start_angle : int
    Rest position each bounce starts and ends at, in degrees (default 0 —
    see Motion model for why). Must satisfy start_angle + amplitude <= 180.
angle_step_deg : int
    Degrees added to angle_deg per iteration (default 0 — no rotation,
    the original fixed-direction behaviour). See Rotation.
"""

import math
import time

import orchestrator
from server import settings
from server.modes import envelope
from server.patterns.helpers import one_sided_bounce_ms, projection_fractions, settle_ms, stage_bounce
from server.patterns.layout import ALL_UNITS

_TICK_S = 0.1

# Margin added on top of one_sided_bounce_ms(180) below, so the default
# amplitude stays safe at the floor sweep_ms without every caller having to
# recompute the exact bounce time themselves.
_SWEEP_MARGIN_MS = 50


def min_sweep_ms() -> int:
    """Floor for sweep_ms: the minimum time a full-amplitude (180°)
    one-sided bounce takes to complete at the currently configured speed
    (see helpers.one_sided_bounce_ms) plus margin. ~4355ms at amplitude=180
    and the 14.5 RPM default; scales with server/settings.py's
    default_rpm() — was a fixed MIN_SWEEP_MS=4400 constant before the
    default speed became configurable."""
    return math.ceil(one_sided_bounce_ms(180) + _SWEEP_MARGIN_MS)


def validate(
    angle_deg: int = 0,
    sweep_ms: int = 6000,
    amplitude: int = 180,
    start_angle: int = 0,
    angle_step_deg: int = 0,
) -> None:
    """Raise ValueError if these params would grow a unit's queue without
    bound, or ask a unit to rise past full extension. Called synchronously
    by the route before starting the mode thread, so a bad request 400s
    instead of silently overflowing or misbehaving."""
    if start_angle + amplitude > 180:
        raise ValueError(
            f"start_angle={start_angle} + amplitude={amplitude} = {start_angle + amplitude}, "
            f"which is past full extension (180°). Lower one or both."
        )

    needed = one_sided_bounce_ms(amplitude)
    if needed > sweep_ms:
        raise ValueError(
            f"amplitude={amplitude} takes ~{needed:.0f}ms to bounce, but sweep_ms={sweep_ms} "
            f"is shorter — a unit wouldn't finish one pass before the next is due, growing its "
            f"queue without bound. Raise sweep_ms to at least {needed:.0f} or lower amplitude."
        )


def plan(angle_deg: int, sweep_ms: int) -> dict[int, float]:
    """Return unit_id -> this unit's offset in ms within one iteration,
    0..sweep_ms, from its position along angle_deg. Pure function, kept
    separate from run() so the schedule itself can be exercised without a
    thread. Called once per iteration by run() — angle_deg is that
    iteration's direction, which drifts across iterations when
    angle_step_deg is nonzero (see Rotation)."""
    fractions = projection_fractions(angle_deg)
    return {unit_id: fractions[unit_id] * sweep_ms for unit_id in ALL_UNITS}


def run(
    stop_event,
    angle_deg: int = 0,
    sweep_ms: int = 6000,
    amplitude: int = 180,
    start_angle: int = 0,
    angle_step_deg: int = 0,
) -> None:
    """Continuously travelling wave; runs until stop_event is set.

    Usage (via server/modes/runner.py, not called directly):
        runner.start("wave", wave.run, {"angle_deg": 0, "sweep_ms": 6000, "amplitude": 180, "start_angle": 0})
    """
    validate(angle_deg, sweep_ms, amplitude, start_angle, angle_step_deg)

    settings.stage_speed()
    orchestrator.absolute(orchestrator.BROADCAST, start_angle)
    orchestrator.go_all()
    if stop_event.wait(settle_ms() / 1000):
        return

    origin = time.monotonic()
    iteration = 0
    iter_start_ms = 0.0
    next_pass_ms = plan(angle_deg, sweep_ms)

    while not stop_event.is_set():
        now_ms = (time.monotonic() - origin) * 1000
        fired = False
        for unit_id in ALL_UNITS:
            if now_ms >= next_pass_ms[unit_id]:
                stage_bounce(unit_id, envelope.scale(amplitude))
                next_pass_ms[unit_id] = float("inf")  # done until the next iteration's recompute
                fired = True
        if fired:
            orchestrator.go_all()

        if now_ms >= iter_start_ms + sweep_ms:
            iteration += 1
            iter_start_ms += sweep_ms
            direction = (angle_deg + iteration * angle_step_deg) % 360
            phase_ms = plan(direction, sweep_ms)
            next_pass_ms = {unit_id: iter_start_ms + phase_ms[unit_id] for unit_id in ALL_UNITS}

        if stop_event.wait(_TICK_S):
            break
