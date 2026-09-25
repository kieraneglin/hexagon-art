"""
server/patterns/pinwheel.py — "Pinwheel" rotational sweep.

A wave rotates around the centre rather than travelling in a straight
line (sweep) or radiating outward (water_drop) — like a radar sweep, a
spinning propeller, or a triskelion, depending on ``arms``.

Direction
---------
Each unit's lag comes from its angular position about the centre
(``atan2`` of its ``layout.pixel()`` coordinate), not its distance.
``arms`` gives the sweep n-fold rotational symmetry: 1 = a single rotating
arm, 2 = a propeller, 3+ = a pinwheel/triskelion. ``spiral_pitch`` blends
in each unit's ring (radius) so the arms bend into a spiral instead of
staying perfectly straight — positive winds outward, negative winds
inward, 0 keeps them straight.

The centre unit (ring 0) sits at (0, 0), where angle is undefined; its
lag is hard-coded to 0 so it always leads every rotation.

Motion model
------------
Same oscillation model as water_drop/cascade — units rest and bob around
90° with per-cycle decay (``helpers.decay_amps`` / ``helpers.stage_cycles``).

Wave timing
-----------
All moves are staged up-front and fired with a single FFG — the
orchestrator never sleeps.

Parameters (all keyword arguments)
-----------------------------------
arms : int
    Rotational symmetry of the sweep (1-6). 1 = single sweep, 2 =
    propeller, 3 = triskelion.
rotation_ms : int
    Milliseconds for one full rotation of the sweep.
spiral_pitch : float
    How much radius bends the arms into a spiral (-2.0 to 2.0). 0 = straight
    arms.
cycles : int
    Number of decaying bob cycles per unit.
start_amplitude : int
    Peak-to-peak amplitude (degrees) on the first cycle.
decay_factor : float
    Amplitude multiplier per cycle (0 < f < 1).
"""

import math

import orchestrator
from server.patterns.helpers import decay_amps, settle_ms, stage_baseline, stage_lagged
from server.patterns.layout import ALL_UNITS, pixel, ring_of


def run(
    arms: int = 2,
    rotation_ms: int = 4400,
    spiral_pitch: float = 0.0,
    cycles: int = 3,
    start_amplitude: int = 180,
    decay_factor: float = 0.8,
) -> None:
    """Execute the pinwheel pattern.

    Usage::

        from server.patterns import pinwheel
        pinwheel.run()                              # 2-arm propeller
        pinwheel.run(arms=3, spiral_pitch=1.0)      # spiraling triskelion
    """
    amps = decay_amps(cycles, start_amplitude, decay_factor)
    if not amps:
        return

    stage_baseline(settle_ms())

    lag_by_unit = {}
    amps_by_unit = {}
    for unit_id in ALL_UNITS:
        x, y = pixel(unit_id)
        if x == 0 and y == 0:
            phase = 0.0
        else:
            theta = (math.atan2(y, x) / (2 * math.pi)) % 1.0
            ring = ring_of(unit_id)
            phase = (theta * arms + spiral_pitch * ring / 4.0) % 1.0
        lag_by_unit[unit_id] = int(round(phase * rotation_ms))
        amps_by_unit[unit_id] = amps
    stage_lagged(lag_by_unit, amps_by_unit, return_to_midpoint=True)

    orchestrator.go_all()
