"""
server/patterns/water_drop.py — "Drop of Water" ripple pattern.

A single impact at the centre radiates outward to surrounding rings, then
the whole pattern repeats with decaying amplitude — like concentric ripples
spreading across still water.

Motion model
------------
Each unit uses a scotch-yoke mechanism: motor rotation → sinusoidal linear
motion.  0° = lowest point of stroke, 180° = highest point.

All units first move to 90° (mid-stroke) as the oscillation baseline.

To avoid a choppy double-move at the transition between cycles (return to
baseline + rise to next peak), the return leg of one cycle and the rise leg
of the next are merged into a single upward command:

  cycle 1 rise   : M+(N1/2)
  cycle 1 fall   : M-N1
  cycle 1→2 join : M+(N1/2 + N2/2)   ← combined return + next rise
  cycle 2 fall   : M-N2
  cycle 2→3 join : M+(N2/2 + N3/2)
  ...
  final return   : M+(Nlast/2)        ← plain return to baseline after last fall

At full amplitude (180°) the first cycle traces: 90 → 180 → 0 → (rise merged
into next cycle).

Wave timing
-----------
The entire pattern is staged into each unit's queue up-front, then fired
with a single FFG.  Units drain their queues sequentially, so all cycles
play back automatically without any further orchestrator intervention.

A single W<delay> is prepended to each outer ring's queue before any moves
are staged.  This one offset shifts the entire ring queue:

  centre  :       A90  M+90 M-180 M+150 M-120 M+90 M-60 M+30  A90
  ring 1  : W600  A90  M+90 M-180 M+150 M-120 M+90 M-60 M+30  A90

  (example: 3 cycles at 180→120→60, joins shown as merged upward moves)
  FFG  ← fires once; all timing lives in the unit queues

All rings in a cycle share the same amplitude — decay is per-cycle only.

Decay
-----
Each successive cycle multiplies amplitude by decay_factor.  Cycles with
amplitude below MIN_AMPLITUDE are skipped.
A final A90 returns all units to mid-stroke.

Parameters (all keyword arguments)
-----------------------------------
cycles : int
    Number of decaying ripple waves to emit.
start_amplitude : int
    Peak-to-peak amplitude (degrees) on the first cycle.  Half this value
    is used for the up/return legs; the full value for the down leg.
decay_factor : float
    Multiplier applied to amplitude each cycle (0 < f < 1).
ring_delay_ms : int
    Milliseconds of lag added per ring from centre outward.
"""

import orchestrator
from server.patterns.helpers import MIDPOINT, decay_amps, stage_lagged
from server.patterns.layout import RINGS


def run(
    cycles: int = 4,
    start_amplitude: int = 180,
    decay_factor: float = 0.667,
    ring_delay_ms: int = 600,
) -> None:
    """Execute the water-drop pattern.

    Stages the entire pattern into each unit's queue, then fires once with
    FFG.  All timing lives in the unit queues — the orchestrator never sleeps.

    Usage::

        from server.patterns import water_drop
        water_drop.run()                              # defaults
        water_drop.run(cycles=4, ring_delay_ms=300)   # custom
    """
    # Pre-compute the list of valid cycle amplitudes.
    amps = decay_amps(cycles, start_amplitude, decay_factor)

    if not amps:
        return

    # Move all units to mid-stroke baseline immediately before staging begins.
    orchestrator.absolute(orchestrator.BROADCAST, MIDPOINT)

    # Stage a one-time lag on each outer ring, then queue all oscillations.
    lag_by_unit = {}
    amps_by_unit = {}
    for ring_idx, ring in enumerate(RINGS):
        lag = ring_idx * ring_delay_ms
        for unit_id in ring:
            lag_by_unit[unit_id] = lag
            amps_by_unit[unit_id] = amps
    stage_lagged(lag_by_unit, amps_by_unit, return_to_midpoint=False)

    # Fire everything — one FFG kicks off the entire sequence.
    orchestrator.go_all()
