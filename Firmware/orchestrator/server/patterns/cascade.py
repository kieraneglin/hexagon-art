"""
server/patterns/cascade.py — "Cascade" sequential ripple pattern.

Units fire one after another in wiring order (ALL_UNITS), each offset by
unit_delay_ms from the previous.  Every unit bobs up and down a few times
with decaying amplitude before settling back at mid-stroke (90°).

Motion model
------------
Identical to water_drop: units start at 90° (mid-stroke) and each cycle
oscillates around that midpoint with merged inter-cycle transitions:

  M+(amp/2)
  M-amp  →  M+(amp/2 + next_amp/2)  (merged return + next rise)
  ...
  M-amp_last  →  M+(amp_last/2)     (plain return after last fall)

Timing
------
All moves are staged up-front and fired with a single FFG.  Each unit's
queue begins with a W<N × unit_delay_ms> so they cascade without any
time.sleep on the orchestrator side:

  unit 0 :           A90  M+90 M-180 …  A90
  unit 1 : W400      A90  M+90 M-180 …  A90
  unit 2 : W800      A90  M+90 M-180 …  A90
  …

Parameters (all keyword arguments)
-----------------------------------
cycles : int
    Number of decaying bob cycles per unit.
start_amplitude : int
    Peak-to-peak amplitude (degrees) on the first cycle.
decay_factor : float
    Amplitude multiplier per cycle (0 < f < 1).
unit_delay_ms : int
    Milliseconds between successive units starting.
"""

import orchestrator
from server.patterns.helpers import MIDPOINT, decay_amps, stage_lagged
from server.patterns.layout import ALL_UNITS


def run(
    cycles: int = 3,
    start_amplitude: int = 180,
    decay_factor: float = 0.667,
    unit_delay_ms: int = 400,
) -> None:
    """Execute the cascade pattern.

    Stages the entire sequence up-front, then fires once with FFG.

    Usage::

        from server.patterns import cascade
        cascade.run()
        cascade.run(cycles=4, unit_delay_ms=300)
    """
    # Pre-compute valid cycle amplitudes.
    amps = decay_amps(cycles, start_amplitude, decay_factor)

    if not amps:
        return

    # Move all units to mid-stroke baseline immediately before staging begins.
    orchestrator.absolute(orchestrator.BROADCAST, MIDPOINT)

    lag_by_unit = {}
    amps_by_unit = {}
    for unit_idx, unit_id in enumerate(ALL_UNITS):
        lag_by_unit[unit_id] = unit_idx * unit_delay_ms
        amps_by_unit[unit_id] = amps
    stage_lagged(lag_by_unit, amps_by_unit, return_to_midpoint=True)

    orchestrator.go_all()
