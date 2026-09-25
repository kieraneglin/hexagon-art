"""
server/patterns/bloom.py — "Bloom" breathing dome.

Unlike the travelling-wave patterns (water_drop, cascade, sweep,
pinwheel), Bloom's shape comes from amplitude, not timing: every unit
starts nearly together, but each ring gets its own amplitude, so the
surface inflates and deflates as a dome — or, with the amplitudes
inverted, a bowl.

Shape
-----
Amplitude is linearly interpolated per ring between ``centre_amplitude``
(ring 0) and ``edge_amplitude`` (ring 4). Setting ``edge_amplitude``
higher than ``centre_amplitude`` inverts the shape from a dome to a bowl.
An optional small ``ring_lead_ms`` staggers rings so the dome visibly
swells outward from the centre rather than breathing perfectly rigidly;
0 makes every unit start in lockstep.

Motion model
------------
Same oscillation model as water_drop/cascade — each ring bobs around 90°
with its own per-cycle decay (``helpers.decay_amps`` / ``helpers.stage_cycles``).
Rings whose interpolated amplitude falls below MIN_AMPLITUDE simply don't
move — this is a calmer, quieter counterpoint to the travelling patterns.

Wave timing
-----------
All moves are staged up-front and fired with a single FFG — the
orchestrator never sleeps.

Parameters (all keyword arguments)
-----------------------------------
centre_amplitude : int
    Peak-to-peak amplitude (degrees) at the centre (ring 0), first cycle.
edge_amplitude : int
    Peak-to-peak amplitude (degrees) at the outermost ring, first cycle.
    Higher than centre_amplitude inverts dome to bowl.
ring_lead_ms : int
    Milliseconds of lag added per ring from centre outward. 0 = all rings
    breathe in lockstep.
cycles : int
    Number of decaying breath cycles.
decay_factor : float
    Amplitude multiplier per cycle (0 < f < 1).
"""

import orchestrator
from server.patterns.helpers import decay_amps, settle_ms, stage_baseline, stage_lagged
from server.patterns.layout import RINGS


def run(
    centre_amplitude: int = 180,
    edge_amplitude: int = 60,
    ring_lead_ms: int = 150,
    cycles: int = 4,
    decay_factor: float = 0.85,
) -> None:
    """Execute the bloom pattern.

    Usage::

        from server.patterns import bloom
        bloom.run()                                          # dome
        bloom.run(centre_amplitude=60, edge_amplitude=180)   # bowl
    """
    stage_baseline(settle_ms())

    last_ring = len(RINGS) - 1

    lag_by_unit = {}
    amps_by_unit = {}
    for ring_idx, ring in enumerate(RINGS):
        t = ring_idx / last_ring if last_ring > 0 else 0.0
        ring_start_amplitude = centre_amplitude + (edge_amplitude - centre_amplitude) * t
        ring_amps = decay_amps(cycles, ring_start_amplitude, decay_factor)
        lag = ring_idx * ring_lead_ms
        for unit_id in ring:
            lag_by_unit[unit_id] = lag
            amps_by_unit[unit_id] = ring_amps
    stage_lagged(lag_by_unit, amps_by_unit, return_to_midpoint=True)

    orchestrator.go_all()
