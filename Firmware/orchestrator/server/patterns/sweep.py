"""
server/patterns/sweep.py — "Sweep" directional travelling wave.

A wave flows across the whole piece in a straight line from one edge (or
corner) to the opposite one — e.g. west to east, or corner to corner —
rather than radiating from the centre (water_drop) or following wiring
order (cascade).

Motion model
------------
Identical oscillation model to water_drop/cascade: units rest and bob
around 90° (mid-stroke), decaying amplitude each cycle
(``helpers.decay_amps`` / ``helpers.stage_cycles``).

Direction
---------
Each unit's physical (x, y) centre (``layout.pixel()``) is projected onto
a unit vector at ``direction_deg``. Projections are normalised to [0, 1]
across the whole piece and scaled by ``sweep_ms``, so ``sweep_ms`` always
means "time for the wave to cross the piece", regardless of direction.

``direction_deg`` is measured in the same (x, y) space ``pixel()`` uses,
where +y runs south (see layout.py) — so 0° sweeps west→east, 90° sweeps
north→south (flat edge to flat edge), and the diagonals in between (60°,
120°, ...) sweep corner to corner. Default is 60° — the corner-to-corner
flow used as the reference example for this pattern.

Wave timing
-----------
All moves are staged up-front and fired with a single FFG, exactly like
water_drop/cascade — the orchestrator never sleeps.

Parameters (all keyword arguments)
-----------------------------------
direction_deg : int
    Sweep heading in degrees (0-330, step 30). 0 = W->E, 90 = N->S, 60/120
    = corner to corner.
sweep_ms : int
    Milliseconds for the wave to cross the entire piece.
cycles : int
    Number of decaying bob cycles per unit.
start_amplitude : int
    Peak-to-peak amplitude (degrees) on the first cycle.
decay_factor : float
    Amplitude multiplier per cycle (0 < f < 1).
"""

import orchestrator
from server.patterns.helpers import decay_amps, projection_fractions, settle_ms, stage_baseline, stage_lagged
from server.patterns.layout import ALL_UNITS


def run(
    direction_deg: int = 60,
    sweep_ms: int = 4000,
    cycles: int = 3,
    start_amplitude: int = 180,
    decay_factor: float = 0.75,
) -> None:
    """Execute the sweep pattern.

    Usage::

        from server.patterns import sweep
        sweep.run()                                   # corner to corner
        sweep.run(direction_deg=0, sweep_ms=6000)     # slow west to east
    """
    amps = decay_amps(cycles, start_amplitude, decay_factor)
    if not amps:
        return

    fractions = projection_fractions(direction_deg)

    stage_baseline(settle_ms())

    lag_by_unit = {}
    amps_by_unit = {}
    for unit_id in ALL_UNITS:
        lag_by_unit[unit_id] = int(round(fractions[unit_id] * sweep_ms))
        amps_by_unit[unit_id] = amps
    stage_lagged(lag_by_unit, amps_by_unit, return_to_midpoint=True)

    orchestrator.go_all()
