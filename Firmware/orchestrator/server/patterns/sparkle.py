"""
server/patterns/sparkle.py — "Sparkle" seeded-random shimmer.

Each unit independently gets a random start delay, a random amplitude
scale, and a random chance of participating at all — a shimmering,
uncoordinated texture rather than a coherent wave.

Randomness
----------
Uses ``helpers.rand_stream`` — a small integer LCG, not the stdlib/
CircuitPython ``random`` module. Those use different PRNGs, so the same
seed would choreograph differently in the browser simulator (CPython)
than on the real units (CircuitPython); the LCG here is identical integer
arithmetic on both, so a seed previewed in the simulator plays back
exactly on the wall. Draws happen in ``layout.ALL_UNITS`` order (three
draws per unit: participate, lag, amplitude scale) so the sequence is
stable regardless of what other parameters are set to.

Motion model
------------
Same oscillation model as water_drop/cascade — units rest and bob around
90° with per-cycle decay (``helpers.decay_amps`` / ``helpers.stage_cycles``).
Non-participating units (roll > density) simply never move.

Wave timing
-----------
All moves are staged up-front and fired with a single FFG — the
orchestrator never sleeps.

Parameters (all keyword arguments)
-----------------------------------
seed : int
    PRNG seed. Same seed -> same choreography, in the simulator and on
    the wall alike.
spread_ms : int
    Upper bound (exclusive) on each participating unit's random start delay.
density : float
    Fraction of units that participate at all (0.1-1.0).
amp_jitter : float
    Lower bound of each unit's random amplitude scale (0.1-1.0); the
    upper bound is always 1.0.
cycles : int
    Number of decaying bob cycles per participating unit.
start_amplitude : int
    Peak-to-peak amplitude (degrees) on the first cycle, before jitter.
decay_factor : float
    Amplitude multiplier per cycle (0 < f < 1).
"""

import orchestrator
from server.patterns.helpers import decay_amps, rand_stream, settle_ms, stage_baseline, stage_lagged
from server.patterns.layout import ALL_UNITS


def run(
    seed: int = 1,
    spread_ms: int = 3000,
    density: float = 0.6,
    amp_jitter: float = 0.4,
    cycles: int = 2,
    start_amplitude: int = 150,
    decay_factor: float = 0.6,
) -> None:
    """Execute the sparkle pattern.

    Usage::

        from server.patterns import sparkle
        sparkle.run()                          # default shimmer
        sparkle.run(seed=42, density=0.3)      # sparser, different texture
    """
    rand = rand_stream(seed)

    lag_by_unit = {}
    amps_by_unit = {}
    for unit_id in ALL_UNITS:
        roll_participate = rand()
        roll_lag = rand()
        roll_scale = rand()

        if roll_participate > density:
            amps_by_unit[unit_id] = []
            continue

        lag_by_unit[unit_id] = int(round(roll_lag * spread_ms))
        scale = amp_jitter + roll_scale * (1.0 - amp_jitter)
        amps_by_unit[unit_id] = decay_amps(cycles, start_amplitude, decay_factor, scale=scale)

    stage_baseline(settle_ms())
    stage_lagged(lag_by_unit, amps_by_unit, return_to_midpoint=True)

    orchestrator.go_all()
