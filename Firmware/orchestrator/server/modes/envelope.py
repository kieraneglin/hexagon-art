"""
server/modes/envelope.py — process-global amplitude fade for auto mode.

server/modes/auto.py crossfades between sub-modes (wave/ripple/random) by
scaling every bounce's amplitude down to silence and back up, rather than
cutting motion abruptly at each segment boundary. The three sub-mode tick
loops (wave.py, ripple.py, shimmer.py) are the only things that read this —
each multiplies its own `amplitude` through `scale()` at its one
`helpers.stage_bounce` call site. Deliberately not folded into
`stage_bounce` itself: that helper is shared with server/patterns/, and a
global that silently scales pattern motion too is a worse trade than three
explicit call sites in the mode layer that actually needs it.

Only ever a plain float in [0.0, 1.0], written and read from one thread at a
time in practice (only server/modes/auto.py's supervisor thread calls
set_gain/reset — see its module docstring), and CPython attribute
reads/writes are already atomic, so no lock is needed here the way
runner.py's globals need one.
"""

from server.patterns.helpers import MIN_AMPLITUDE

_gain = 1.0


def set_gain(g: float) -> None:
    """Set the current fade gain, clamped to [0.0, 1.0].

    Clamped rather than trusted: scale() is only safe against every mode's
    queue-safety validate() when it can only ever *shrink* a bounce (see
    scale()'s docstring) — a gain above 1.0 would grow one instead.
    """
    global _gain
    _gain = max(0.0, min(1.0, g))


def reset() -> None:
    """Reset the fade gain to 1.0 (full amplitude, no scaling)."""
    set_gain(1.0)


def gain() -> float:
    """Return the current fade gain."""
    return _gain


def scale(amplitude: int) -> int:
    """Apply the current fade gain to a bounce amplitude.

    Returns 0 (stage_bounce's no-op case) rather than a sub-MIN_AMPLITUDE
    value — see server/patterns/helpers.py on why M+0 (and anything that
    would round to it) must never be emitted.

    Because gain is always in [0.0, 1.0], this only ever shrinks amplitude,
    never grows it — so it can't turn a validated-safe amplitude into one
    that violates a mode's own one_sided_bounce_ms(amplitude) <= period
    check (see wave.py/ripple.py/shimmer.py's validate()).
    """
    scaled = int(round(amplitude * _gain))
    return scaled if scaled >= MIN_AMPLITUDE else 0
