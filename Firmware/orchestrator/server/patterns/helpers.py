"""
server/patterns/helpers.py — Shared staging helpers for pattern modules.

All helpers stage commands onto individual units without sending G.
Call ``orchestrator.go_all()`` (FFG) after staging a full wave to fire
all units simultaneously.

Units use a scotch-yoke mechanism, so motor rotation is translated into
sinusoidal linear motion.  A move of +180° brings the unit from its lowest
point (0°) to its highest point; -180° returns it to the lowest point.

Conventions
-----------
- Angles are integers in degrees (1–65535 for M, 0–359 for A).
- Amplitude of 0 is a no-op (skipped — the firmware discards zero-degree
  M commands anyway, but we skip them early for clarity).
- ``ring_delay_ms`` is inserted as a W command *before* the motion commands
  on ring units so the ring lags behind the centre in a single FFG wave.

Timing/queue constraints these helpers are shaped around
----------------------------------------------------------
- The firmware's per-unit queue holds 64 staged commands; overflow is
  silently discarded and invisible on real hardware (see
  ``simhost/selftest.py``, which is the only thing that catches it).
- Consecutive same-direction M commands merge into one queue slot
  (degrees summed) — any W/S/H between them breaks the merge chain.
  Oscillations must alternate sign to survive as distinct moves; this is
  why ``stage_cycles`` below produces merged up/down/up transitions
  rather than naive per-cycle up-down pairs.
- M+0 is malformed and silently dropped by the firmware, so every helper
  that scales amplitude floors at MIN_AMPLITUDE rather than ever letting
  a scaled value round down to 0.
- Move duration follows ``move_ms`` below, at whatever speed
  ``server/settings.py``'s ``default_rpm()`` currently holds (14.5 RPM,
  the anti-resonance default, unless changed on /admin/tools) — a full
  180° move takes ~2.18s at that speed, so a full bounce cycle at max
  amplitude is ~4.4s. This is the timescale pattern authors should tune
  delay parameters against; it stretches or shrinks with the configured
  speed.
"""

import math

import orchestrator
from server import settings
from server.patterns.layout import ALL_UNITS, pixel

# Amplitudes below this threshold are treated as zero (no-op) — also the
# floor that keeps a per-unit amplitude scale from ever producing M+0.
MIN_AMPLITUDE = 10

# Mid-stroke baseline: patterns rest and oscillate around this angle, where
# scotch-yoke depth response is most linear (depth = 15*(1-cos(angle))).
MIDPOINT = 90


def move_ms(degrees: float, rpm: float = None) -> float:
    """Estimate how long a single M/A move of `degrees` takes, in ms, at
    `rpm` (default: the configured server/settings.py default_rpm()).

    Mirrors src/sim/unit.js's closed-form trapezoid/triangle motion
    profile (itself derived from the firmware's AccelStepper ramp):
    9.56ms lead-in, plus either a full accel/cruise/decel trapezoid or,
    for short moves, a triangle that never reaches max speed.
    """
    if rpm is None:
        rpm = settings.default_rpm()
    if degrees <= 0:
        return 0.0
    # Triangle/trapezoid crossover: deg < 0.0410 * rpm**2 never reaches
    # cruise speed (e.g. 8.6° at 14.5 RPM).
    if degrees < 0.0410 * rpm * rpm:
        steps = round((degrees * 4096 + 180) / 360)
        return 9.56 + 20 * (steps**0.5)
    return 9.56 + 166.667 * (degrees / rpm) + 6.827 * rpm


def settle_ms() -> float:
    """Worst-case single A move (180°, at the currently configured speed —
    see move_ms) rounded up with ~1% margin. The settle time
    stage_baseline/every mode's run() waits after its opening broadcast so
    every unit reaches baseline before any pattern-specific lag/motion
    begins, instead of the wavefront being smeared by pre-existing angle
    variance across units. Was a fixed SETTLE_MS=2200 constant (2178ms at
    14.5 RPM) before the default speed became configurable."""
    return math.ceil(move_ms(180) * 1.01)


def one_sided_bounce_ms(amplitude: int) -> float:
    """Wall-clock time one stage_bounce(uid, amplitude) bounce takes to
    drain: M+amplitude, M-amplitude — two legs, both at the full amplitude
    (contrast stage_cycles' symmetric-around-a-midpoint shape, which pays
    move_ms's fixed per-move overhead three times for the same total
    angular distance instead of twice).

    Shared by server/modes/wave.py and server/modes/shimmer.py — both need
    this to size a safety floor (a unit must finish one bounce before it
    can safely be re-triggered, or staged commands accumulate in its
    64-slot queue without bound).
    """
    return 2 * move_ms(amplitude)


def decay_amps(cycles: int, start_amplitude: float, decay_factor: float, scale: float = 1.0) -> list:
    """Compute per-cycle amplitudes, decaying by decay_factor each cycle.

    `scale` multiplies the whole envelope before decay is applied — lets a
    caller give each unit its own amplitude (e.g. bloom's per-ring lerp,
    sparkle's per-unit jitter) while sharing this one decay loop. Cycles
    that would round below MIN_AMPLITUDE are dropped (this is the M+0
    guard — every amplitude that survives is safe to halve and stage).
    """
    amps = []
    amplitude = float(start_amplitude) * scale
    for _ in range(cycles):
        amp = int(round(amplitude))
        if amp < MIN_AMPLITUDE:
            break
        amps.append(amp)
        amplitude *= decay_factor
    return amps


def projection_fractions(direction_deg: float) -> dict[int, float]:
    """Project every unit's (x, y) centre onto a unit_id -> [0, 1] fraction
    along `direction_deg`, where 0.0 is the piece's trailing edge in that
    direction and 1.0 is its leading edge.

    Shared by sweep.py and server/modes/wave.py — both need "how far along
    this heading is this unit" to turn a direction + a total crossing time
    into a per-unit lag. `direction_deg` is measured in the same (x, y)
    space layout.pixel() uses, where +y runs south — so 0 sweeps west to
    east, 90 sweeps north to south, and 60/120 sweep corner to corner.

    Units are spatially degenerate along an axis orthogonal to
    direction_deg only when the whole piece has zero extent along
    direction_deg (a single unit); every fraction is 0.0 in that case.
    """
    ux = math.cos(math.radians(direction_deg))
    uy = math.sin(math.radians(direction_deg))

    proj = {unit_id: x * ux + y * uy for unit_id, (x, y) in ((u, pixel(u)) for u in ALL_UNITS)}
    lo = min(proj.values())
    span = max(proj.values()) - lo

    if span <= 0:
        return {unit_id: 0.0 for unit_id in ALL_UNITS}
    return {unit_id: (p - lo) / span for unit_id, p in proj.items()}


def radial_fractions() -> dict[int, float]:
    """Map every unit_id -> its Euclidean distance from the centre unit,
    normalised to [0, 1] against the farthest unit.

    Shared by server/patterns/home_wave.py and server/modes/ripple.py —
    both need "how far from centre is this unit" to turn a single spread
    time into a smoothly curving (not ring-stepped) per-unit lag, the same
    role projection_fractions() plays for a directional sweep.

    Degenerate only when every unit sits at the same distance (a
    single-unit layout); every fraction is 0.0 in that case, mirroring
    projection_fractions()'s zero-span guard.
    """
    distances = {unit_id: math.hypot(*pixel(unit_id)) for unit_id in ALL_UNITS}
    farthest = max(distances.values())
    if farthest <= 0:
        return {unit_id: 0.0 for unit_id in ALL_UNITS}
    return {unit_id: d / farthest for unit_id, d in distances.items()}


def stage_cycles(unit_id: int, amps: list) -> None:
    """Stage a full run of oscillation cycles for one unit around MIDPOINT.

    Instead of M+(N/2) M-N M+(N/2) | M+(N2/2) M-N2 M+(N2/2) ..., the return
    leg of each cycle is merged with the rise leg of the next, producing a
    single uninterrupted upward move at each transition — both smoother
    motion and half the queue slots:

      M+(amps[0]//2)
      for each consecutive pair (a, b):
          M-a
          M+(a//2 + b//2)   ← merged return + rise
      M-amps[-1]
      M+(amps[-1]//2)       ← plain return after last fall
    """
    if not amps:
        return

    orchestrator.move(unit_id, +(amps[0] // 2))

    for i, amp in enumerate(amps):
        orchestrator.move(unit_id, -amp)
        if i < len(amps) - 1:
            orchestrator.move(unit_id, +(amp // 2 + amps[i + 1] // 2))
        else:
            orchestrator.move(unit_id, +(amp // 2))


def rand_stream(seed: int):
    """Return a zero-argument callable yielding successive floats in [0, 1).

    A plain 32-bit LCG (Numerical Recipes constants) rather than the
    stdlib `random` module, so the sequence for a given seed is pinned to
    this exact integer arithmetic instead of whatever `random`'s own
    implementation happens to do on a given Python version. A seed
    previewed in the browser simulator is guaranteed to be the exact
    sequence that plays on the wall.
    """
    state = [seed & 0xFFFFFFFF]

    def _next() -> float:
        state[0] = (state[0] * 1664525 + 1013904223) & 0xFFFFFFFF
        return state[0] / 4294967296.0

    return _next


def stage_baseline(settle_ms: int = 0) -> None:
    """Broadcast all units to MIDPOINT, then optionally wait for the move
    to complete before any further per-unit staging begins.

    A90 is itself a staged command (lands as slot 0 in every queue), so
    without a settle wait, units starting far from 90° reach baseline up
    to ~2.2s after units already near it — smearing the start of whatever
    wave follows. Costs one extra queue slot per unit when settle_ms > 0.
    """
    orchestrator.absolute(orchestrator.BROADCAST, MIDPOINT)
    if settle_ms > 0:
        orchestrator.wait_ms(orchestrator.BROADCAST, settle_ms)


def stage_lagged(lag_by_unit: dict, amps_by_unit: dict, return_to_midpoint: bool = True) -> None:
    """Stage a per-unit-delayed oscillation wave across many units.

    `amps_by_unit` is iterated in insertion order, so callers control
    emission order by how they build the dict (e.g. ring order, wiring
    order). Units mapped to an empty/missing amps list are skipped
    entirely — no wait, no motion. Does not fire; call
    ``orchestrator.go_all()`` after.
    """
    for unit_id, amps in amps_by_unit.items():
        if not amps:
            continue
        lag = lag_by_unit.get(unit_id, 0)
        if lag > 0:
            orchestrator.wait_ms(unit_id, lag)
        stage_cycles(unit_id, amps)
        if return_to_midpoint:
            orchestrator.absolute(unit_id, MIDPOINT)


def stage_events(events_by_unit: dict, max_slots: int = 48) -> None:
    """Stage an independent list of (t_ms, amplitude) impact events per unit.

    For patterns where a unit can be hit more than once (e.g. rain), rather
    than one continuous oscillation. Events are sorted by time and staged
    as single-cycle bobs (rise/fall/return, 3 M commands each) separated
    by a W for the gap since the previous bob finished.

    If a bob's finish time is at or after the next event's start (gap <=
    0), no W is emitted — the previous bob's trailing return move and the
    next bob's leading rise move are both M+, so they merge into one
    continuous upward move per the firmware's merge rule. This reproduces
    stage_cycles' own smooth-transition idiom and is deliberate: overlap
    reads as continuous motion, not a bug to suppress.

    Stops adding events to a unit once it would exceed `max_slots`,
    leaving headroom under the real 64-slot cap.
    """
    for unit_id, events in events_by_unit.items():
        if not events:
            continue
        cursor_ms = 0.0
        slots_used = 0
        for t_ms, amplitude in sorted(events, key=lambda e: e[0]):
            amp = int(round(amplitude))
            if amp < MIN_AMPLITUDE:
                continue
            if slots_used + 3 > max_slots:
                break
            gap = t_ms - cursor_ms
            if gap > 0:
                orchestrator.wait_ms(unit_id, int(round(gap)))
                slots_used += 1
            stage_cycles(unit_id, [amp])
            slots_used += 3
            half = amp // 2
            cursor_ms = t_ms + move_ms(half) + move_ms(amp) + move_ms(half)


def stage_bounce(unit_id: int, amplitude: int) -> None:
    """Stage a full ±amplitude bounce for one unit.

    Queues:  M+<amplitude>  then  M-<amplitude>
    Does nothing if amplitude <= 0.
    """
    if amplitude <= 0:
        return
    orchestrator.move(unit_id, +amplitude)
    orchestrator.move(unit_id, -amplitude)


def stage_wave(
    rings: list[list[int]],
    amplitudes: list[int],
    ring_delay_ms: int = 0,
) -> None:
    """Stage one wave across all rings with optional inter-ring delay.

    Each ring[i] gets amplitude amplitudes[i].  If amplitudes is shorter
    than rings, remaining rings get amplitude 0 (skipped).

    The delay is cumulative: ring 1 waits ring_delay_ms, ring 2 waits
    2×ring_delay_ms, etc.  Ring 0 (centre) always has no delay.

    After calling this, issue ``orchestrator.go_all()`` to fire the wave.
    """
    for ring_idx, ring in enumerate(rings):
        amp = amplitudes[ring_idx] if ring_idx < len(amplitudes) else 0
        delay = ring_idx * ring_delay_ms

        for unit_id in ring:
            if delay > 0:
                orchestrator.wait_ms(unit_id, delay)
            stage_bounce(unit_id, amp)


def fire_wave(
    rings: list[list[int]],
    amplitudes: list[int],
    ring_delay_ms: int = 0,
) -> None:
    """Stage a wave and immediately fire all units (FFG)."""
    stage_wave(rings, amplitudes, ring_delay_ms)
    orchestrator.go_all()
