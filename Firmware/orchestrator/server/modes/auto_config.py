"""
server/modes/auto_config.py — auto mode's rotation, tuned by hand.

server/modes/auto.py reads this file at import time. It changes rarely
enough (tuning how the installation behaves unattended) that living in code
rather than a runtime-editable JSON file is the right trade — see README.md's
modes-vs-patterns section for why auto exists at all.

SEGMENTS is played in order, forever (see SHUFFLE below to randomise the
order instead). Each entry:
    mode : one of "wave", "ripple", "random" — the only sub-modes that
        route their motion through helpers.stage_bounce, which is what lets
        server/modes/envelope.py fade them in/out (see envelope.py's
        docstring). "life" stages absolute A moves instead and isn't
        fade-aware, so it's deliberately not usable here —
        auto.validate() rejects it by name if added.
    minutes : how long this segment holds at full amplitude, not counting
        the fade in/out (float, so a segment can be shortened for testing).
    params : keyword overrides for that sub-mode's run()/validate() —
        anything omitted falls back to the sub-mode's own default (see
        server/modes/wave.py, ripple.py, shimmer.py). Must satisfy that
        sub-mode's own validate(**params); auto.validate() checks this
        synchronously so a bad edit here 400s at the "Auto" button instead
        of failing silently in the background thread.

        Time-valued params (sweep_ms, period_ms, spread_ms, interval_ms)
        below are authored at settings.REFERENCE_RPM (14.5) — auto.py's
        _scaled_params() multiplies them by settings.speed_scale() before
        validating/running, so a segment authored here with just enough
        headroom over its sub-mode's queue-safety floor keeps roughly that
        same headroom at whatever speed /admin/tools has configured,
        rather than needing to be authored defensively wide for the
        slowest speed anyone might ever dial in.
"""

SEGMENTS = [
    {
        "mode": "wave",
        "minutes": 6.0,
        "params": {"sweep_ms": 5500, "amplitude": 180, "angle_step_deg": 5},
    },
    {
        "mode": "ripple",
        "minutes": 6.0,
        "params": {"period_ms": 5000, "spread_ms": 3000, "amplitude": 150},
    },
    {
        "mode": "random",
        "minutes": 4.0,
        "params": {
            "interval_ms": 5000,
            "density": 0.85,
            "min_amplitude": 120,
            "max_amplitude": 180,
        },
    },
]

# True: reshuffle the rotation order at the start of every cycle through
# SEGMENTS (never repeating the segment that just ended back-to-back).
# False: always play SEGMENTS in the order written above.
SHUFFLE = False

# How long (ms) a segment takes to ramp from silent to full amplitude, and
# from full amplitude back to silent, via server/modes/envelope.py.
FADE_IN_MS = 4000
FADE_OUT_MS = 4000

# Stillness held between one segment's fade-out and the next segment's own
# opening broadcast (every sub-mode's run() already settles for
# helpers.settle_ms() on top of this — see auto.py's _play()).
GAP_MS = 2000

# How long (ms) auto.py's _play() waits after broadcasting FFH + FFG before
# starting the next segment's sub-mode — every unit homes between segments
# so position error can't accumulate across a rotation meant to run for
# days (see _play()'s "homing" phase). Sized for a worst-case unconditional
# home: a full-revolution endstop search, ~4.25s at settings.REFERENCE_RPM
# (14.5, see server/patterns/home_wave.py's docstring) — _play() scales
# this by settings.speed_scale() so a slower configured speed gets the
# correspondingly longer wait a real endstop search takes at that speed.
# Homing is deaf to the UART while it runs, so the next segment's opening
# broadcast must not go out until this has actually drained, not just
# "probably long enough."
HOME_SETTLE_MS = 5000

# Whether main.py starts auto mode automatically shortly after boot — see
# main.py for the HEXAGON_AUTO_START env var that overrides this.
START_ON_BOOT = True

# Seconds main.py waits after boot before arming auto — gives the UART/
# service a moment to settle before the first command goes out.
START_DELAY_S = 10
