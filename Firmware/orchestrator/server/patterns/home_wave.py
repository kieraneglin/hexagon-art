"""
server/patterns/home_wave.py — staggered wave-homing for all units.

Not a choreography pattern — a system operation triggered by the top bar's
persistent Home button (see server/routes/patterns.py; deliberately absent
from PATTERN_META so it never shows up as a card on the Patterns page).
Homing all 61 units at once would spike the power supply (every unit's
endstop-search current draw landing in the same instant); staggering
outward from the centre smooths that out, the same way sweep/water_drop
stagger their bob waves.

Uses H (unconditional home), not N (conditional home): the Home button's
job is to visibly and reliably home every unit on demand, not to skip
units that don't strictly need it. An earlier version used N here, which
meant most presses only homed whichever units happened to need it — the
"wave" only played on a ragged subset, which is what made it read as
uneven. Patterns still use N for their own pre-run homing (see
server/routes/patterns.py), where draining instantly on the common case
matters more than a guaranteed-visible wave.

Lag is computed as each unit's Euclidean distance from centre (not a flat
per-ring step), so the wavefront curves smoothly outward — a ring-4 corner
unit lags ~13% behind a ring-4 edge-midpoint unit — rather than five
discrete ring-sized jumps. spread_ms's default is sized against a full 360°
homing search at settings.REFERENCE_RPM (~4.25s at 14.5 RPM) so ring onsets
are spaced comparably to how long homing itself takes; too short and the
rings overlap into a single lump, which is what made the old
ring_delay_ms=250 default look broken. Scaled by settings.speed_scale() so
that stays true at whatever speed is currently configured — a caller who
passes an explicit spread_ms is opting out of that scaling, same as any
other explicit override.
"""

import orchestrator
from server import settings
from server.patterns.helpers import radial_fractions
from server.patterns.layout import ALL_UNITS

_DEFAULT_SPREAD_MS = 6000


def run(spread_ms: int = None) -> None:
    """Stage an unconditional home on every unit, lagged by distance from
    centre, then fire.

    The centre unit (distance 0) homes first with no delay; each other
    unit's lag is its distance from centre, normalised to the farthest
    unit (helpers.radial_fractions) and scaled by spread_ms. spread_ms
    defaults to _DEFAULT_SPREAD_MS scaled by settings.speed_scale() — see
    the module docstring.
    """
    if spread_ms is None:
        spread_ms = _DEFAULT_SPREAD_MS * settings.speed_scale()

    fractions = radial_fractions()

    for unit_id in ALL_UNITS:
        lag = int(round(fractions[unit_id] * spread_ms))
        if lag > 0:
            orchestrator.wait_ms(unit_id, lag)
        orchestrator.home(unit_id)

    orchestrator.go_all()
