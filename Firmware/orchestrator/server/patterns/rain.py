"""
server/patterns/rain.py — "Rain" random overlapping impacts.

Several impacts land at random units and random times, each spawning its
own distance-decaying ripple (like water_drop's, but small and local) —
unlike water_drop's single centred impact, drops overlap in time and
space, and a unit hit by more than one drop bobs more than once.

Randomness
----------
Uses ``helpers.rand_stream`` — a seeded LCG, not the stdlib ``random``
module, so a seed previewed in the browser simulator produces the exact
same drops when it actually runs. See helpers.rand_stream's docstring for
why.

Scheduling
----------
Each drop picks a random centre unit and landing time in [0, spread_ms).
Every unit within ``ripple_rings`` of that centre gets an event at
``t_land + distance * ripple_delay_ms`` with amplitude
``start_amplitude * distance_decay**distance``; events below
MIN_AMPLITUDE are dropped. A unit can accumulate events from multiple
drops. ``helpers.stage_events`` then sorts each unit's events by time and
stages them as a sequence of single-cycle bobs — if two events on the
same unit overlap in time, their bobs merge into one continuous motion
(see stage_events' docstring) rather than colliding.

Motion model
------------
Each impact is a single-cycle bounce around 90° (mid-stroke) — rise,
fall, return — rather than the multi-cycle decay used by the other
patterns; the "decay" here is spatial (distance_decay across rings) not
temporal.

Wave timing
-----------
All moves are staged up-front and fired with a single FFG — the
orchestrator never sleeps.

Parameters (all keyword arguments)
-----------------------------------
seed : int
    PRNG seed. Same seed -> same drops, in the simulator and on the wall.
drops : int
    Number of impacts.
spread_ms : int
    Upper bound (exclusive) on each drop's random landing time.
ripple_rings : int
    How many rings out from each drop's centre the ripple reaches.
ripple_delay_ms : int
    Milliseconds of lag per ring-step from a drop's centre.
start_amplitude : int
    Peak-to-peak amplitude (degrees) at a drop's centre.
distance_decay : float
    Amplitude multiplier per ring-step away from a drop's centre (0 < f < 1).
"""

import orchestrator
from server.patterns.helpers import MIN_AMPLITUDE, rand_stream, settle_ms, stage_baseline, stage_events
from server.patterns.layout import ALL_UNITS, distance


def run(
    seed: int = 1,
    drops: int = 5,
    spread_ms: int = 6000,
    ripple_rings: int = 3,
    ripple_delay_ms: int = 400,
    start_amplitude: int = 180,
    distance_decay: float = 0.7,
) -> None:
    """Execute the rain pattern.

    Usage::

        from server.patterns import rain
        rain.run()                              # default light rain
        rain.run(seed=7, drops=8, spread_ms=10000)   # heavier, longer
    """
    rand = rand_stream(seed)

    events_by_unit = {unit_id: [] for unit_id in ALL_UNITS}
    for _ in range(drops):
        idx = min(int(rand() * len(ALL_UNITS)), len(ALL_UNITS) - 1)
        centre_id = ALL_UNITS[idx]
        t_land = rand() * spread_ms

        for unit_id in ALL_UNITS:
            d = distance(centre_id, unit_id)
            if d > ripple_rings:
                continue
            amplitude = start_amplitude * (distance_decay**d)
            if amplitude < MIN_AMPLITUDE:
                continue
            events_by_unit[unit_id].append((t_land + d * ripple_delay_ms, amplitude))

    stage_baseline(settle_ms())
    stage_events(events_by_unit)

    orchestrator.go_all()
