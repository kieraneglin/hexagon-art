"""
server/modes/life.py — cellular automaton across the hex grid.

The set-and-forget counterpart to a discrete choreography burst: instead of
a per-unit schedule (wave.py) or independent per-unit noise (shimmer.py),
every unit's next move here is a function of its neighbours' current state
(server/patterns/layout.py's neighbors()) — the first mode driven by the
piece's topology rather than just its geometry or a fixed timetable.

Rulesets
--------
`ruleset` selects among three simulations, all evaluated over the hex grid's
actual 6-neighbour adjacency (not the 8-neighbour Moore neighbourhood
Conway's own B3/S23 assumes — B3/S23 dies out almost immediately on a
6-neighbour grid):

  0 (default) — Hex Life B2/S34: a cell is born with exactly 2 live
      neighbours, survives with 3 or 4.
  1           — Hex Life B2/S23: born with 2, survives with 2 or 3 — busier
      and more chaotic than ruleset 0.
  2           — Cyclic CA (Greenberg-Hastings): each cell holds one of
      `cycle_states` states in a ring; it advances from k to (k+1) %
      cycle_states once any neighbour already holds (k+1) % cycle_states.
      Self-organises from noise into rotating spiral waves and, unlike 0/1,
      never stagnates on its own — see Anti-stagnation below.

At 61 cells (against Conway's usual effectively-infinite plane), rulesets 0
and 1 reliably collapse to extinction or a static still-life within roughly
5-15 generations from a random start — ring 4 alone is 24 of the 61 units,
so ~40% of the board is edge with only 3-4 on-board neighbours. Whether any
of the three actually reads as interesting on 61 physical tiles is an
empirical question this mode is built to let you answer by eye rather than
settle by argument — hence three rulesets behind one switch rather than
committing to one.

Rendering model
----------------
Raw cell state is never sent to a unit as a target angle directly — a live/
dead flip would read as popping noise, and even the cyclic ruleset's `k`
has a wrap-around discontinuity ((cycle_states-1) -> 0) that would show up
as a full-stroke snap. Each generation:

    raw[u]    = 1.0 if alive else 0.0                                 (0/1)
    raw[u]    = (1 - cos(2*pi*k[u] / cycle_states)) / 2                (2)
    blur[u]   = (1 - smoothing)*raw[u] + smoothing*mean(raw over {u} | neighbors(u))
    target[u] = start_angle + (max_angle - start_angle) * blur[u]
    angle[u] += clamp(target[u] - angle[u], -max_step, +max_step)

`smoothing` (a spatial low-pass over the neighbourhood) turns clusters into
bulges that grow, drift, and dissolve, instead of isolated cells popping in
and out; `max_step` (a temporal rate limit) turns the whole mechanism into
an analog low-pass filter of the digital CA — at the default 45 deg/gen a
full rise takes ~4 generations, so nothing ever snaps, and it's also what
lets a generation tick faster than a unit could traverse its full stroke.
The cyclic ruleset's cosine mapping (rather than a plain k/(cycle_states-1)
sawtooth) is what keeps its wrap-around smooth: a rise-then-fall as each
spiral arm passes, not a snap back to 0.

Anti-stagnation
----------------
Rulesets 0/1 only: every generation, each dead cell has a `spawn_rate`
chance of spontaneous birth (keeps the board perpetually bubbling), and if
the whole board dies out or its alive-set repeats one seen in the last 8
generations (a still-life or short oscillator), the board is reseeded fresh
at `density`. Neither applies to ruleset 2 — an ambient state-advance would
corrupt a spiral arm, and, more importantly, a cleanly rotating spiral *is*
genuinely periodic, so the repeat-detector would trigger on the best
behaviour this mode can produce and destroy it. Cyclic needs no life
support and gets none.

Timing model
------------
Unlike wave.py/shimmer.py, every unit advances on the same clock — one `A`
per unit per generation (skipped for units whose angle changed by less than
1 degree), one FFG. There's a single deadline rather than 61 independent
ones, so the loop waits the exact remaining time to the next generation
rather than polling a short fixed tick; next_gen_ms is advanced by adding
gen_ms to a fixed time.monotonic() origin (never now + gen_ms), same
drift-avoidance idiom as wave.py's next_pass_ms.

Queue-safety guard
-------------------
An FFG separator sits between generations, and validate() guarantees a
unit's move from the previous generation finishes (move_ms(max_step) <=
gen_ms) before the next one is staged — src/sim/unit.js only merges
consecutive MOVE_ABSOLUTE commands while both are still queued, so a move
that's already drained can't merge with anything after it. Each generation
therefore adds and drains exactly one slot per unit; validate() raises
rather than silently allowing a combination that could grow a queue
unbounded, same contract as wave.validate()/shimmer.validate().

Parameters (all keyword arguments)
-----------------------------------
seed : int
    Seed for the shared PRNG (server.patterns.helpers.rand_stream) driving
    the initial board and all reseeds/ambient births. Generations are
    globally synchronous with no wall-clock-dependent draws, so (unlike
    shimmer) the same seed and params reproduce the exact same command
    stream every run.
ruleset : int
    0 = Hex Life B2/S34, 1 = Hex Life B2/S23, 2 = Cyclic CA. See Rulesets.
gen_ms : int
    Milliseconds per generation. Floored by min_gen_ms().
max_step : int
    Maximum degrees a unit's angle may change in one generation. See
    Rendering model / Queue-safety guard.
smoothing : float
    0-1 spatial blend toward the mean raw level of a unit and its
    neighbours. 0 = no blur (raw CA state only); higher smooths clusters
    into gradual bulges.
spawn_rate : float
    Rulesets 0/1 only: per-generation probability a dead cell spontaneously
    is born, independent of the rule's own birth condition.
density : float
    Initial (and reseed) probability a cell starts alive, rulesets 0/1
    only. Ruleset 2 ignores this and seeds every cell to a uniformly random
    state instead.
max_angle : int
    Angle a fully "alive"/peak-phase cell rises to. Must be greater than
    start_angle.
start_angle : int
    Rest position a fully "dead"/trough-phase cell settles at.
cycle_states : int
    Ruleset 2 only: number of states in the cycle. Ignored otherwise.
"""

import collections
import math
import time

import orchestrator
from server import settings
from server.patterns.helpers import move_ms, rand_stream, settle_ms
from server.patterns.layout import ALL_UNITS, neighbors

# Margin added on top of move_ms(_MIN_STEP_DEG) below.
_MIN_STEP_DEG = 25
_GEN_MARGIN_MS = 4


def min_gen_ms() -> int:
    """Floor for gen_ms: move_ms(25) at the currently configured speed,
    rounded up with margin — pairs with a max_step of ~25 deg. Exposed as
    the gen_ms slider's min. Was a fixed MIN_GEN_MS=400 constant (move_ms(25)
    ~= 396ms at 14.5 RPM) before the default speed became configurable."""
    return math.ceil(move_ms(_MIN_STEP_DEG) + _GEN_MARGIN_MS)


# How many past generations' alive-sets to remember for stagnation
# detection (rulesets 0/1 only) — long enough to catch the short-period
# oscillators (period-2 blinkers etc.) that are the common failure mode on
# a board this small, without keeping an unbounded history.
_STAGNATION_HISTORY = 8

# ruleset -> (birth counts, survive counts), Hex Life analogs of Conway's
# B3/S23 for a 6-neighbour grid.
_BIRTH_SURVIVE = {
    0: ({2}, {3, 4}),  # B2/S34
    1: ({2}, {2, 3}),  # B2/S23
}


def validate(
    seed: int = 1,
    ruleset: int = 0,
    gen_ms: int = 900,
    max_step: int = 45,
    smoothing: float = 0.4,
    spawn_rate: float = 0.02,
    density: float = 0.35,
    max_angle: int = 180,
    start_angle: int = 0,
    cycle_states: int = 6,
) -> None:
    """Raise ValueError if these params would grow a unit's queue without
    bound, name an unknown ruleset, or leave the CA no angular range to
    render into. Called synchronously by the route before starting the
    mode thread, so a bad request 400s instead of starting a broken or
    unsafe background thread."""
    if ruleset not in (0, 1, 2):
        raise ValueError(f"ruleset={ruleset} is not a known ruleset (0=B2/S34, 1=B2/S23, 2=Cyclic).")

    if cycle_states < 2:
        raise ValueError(f"cycle_states={cycle_states} must be at least 2.")

    if start_angle >= max_angle:
        raise ValueError(
            f"start_angle={start_angle} must be less than max_angle={max_angle}, "
            f"or the CA has no angular range to render into."
        )

    needed = move_ms(max_step)
    if needed > gen_ms:
        raise ValueError(
            f"max_step={max_step} takes ~{needed:.0f}ms to move, but gen_ms={gen_ms} "
            f"is shorter — a unit could be re-targeted before finishing its previous move, growing its "
            f"queue without bound. Raise gen_ms to at least {needed:.0f} or lower max_step."
        )


def seed_board(rand, ruleset: int, density: float, cycle_states: int) -> dict:
    """Return a fresh unit_id -> state dict.

    Rulesets 0/1: state is 0/1 (dead/alive), each cell alive with
    probability `density`. Ruleset 2: state is a uniformly random
    cycle_states-mod class — cyclic CA has no alive/dead distinction.
    """
    if ruleset == 2:
        return {uid: int(rand() * cycle_states) % cycle_states for uid in ALL_UNITS}
    return {uid: 1 if rand() < density else 0 for uid in ALL_UNITS}


def _advance_life(state: dict, ruleset: int, rand, spawn_rate: float) -> dict:
    birth, survive = _BIRTH_SURVIVE[ruleset]
    new_state = {}
    for uid in ALL_UNITS:
        live_neighbors = sum(1 for nb in neighbors(uid) if state[nb])
        if state[uid]:
            new_state[uid] = 1 if live_neighbors in survive else 0
        else:
            born = live_neighbors in birth
            if not born and spawn_rate > 0 and rand() < spawn_rate:
                born = True
            new_state[uid] = 1 if born else 0
    return new_state


def _advance_cyclic(state: dict, cycle_states: int) -> dict:
    new_state = {}
    for uid in ALL_UNITS:
        k = state[uid]
        nxt = (k + 1) % cycle_states
        new_state[uid] = nxt if any(state[nb] == nxt for nb in neighbors(uid)) else k
    return new_state


def advance(state: dict, ruleset: int, rand, spawn_rate: float, cycle_states: int) -> dict:
    """Return the next generation's state dict. Pure — no orchestrator
    calls — so it (and render()/stage_generation() below) can be exercised
    headlessly by simhost/selftest.py without threads or a background
    tick."""
    if ruleset == 2:
        return _advance_cyclic(state, cycle_states)
    return _advance_life(state, ruleset, rand, spawn_rate)


def _raw_levels(state: dict, ruleset: int, cycle_states: int) -> dict:
    if ruleset == 2:
        return {uid: (1 - math.cos(2 * math.pi * state[uid] / cycle_states)) / 2 for uid in ALL_UNITS}
    return {uid: 1.0 if state[uid] else 0.0 for uid in ALL_UNITS}


def render(
    state: dict,
    angles: dict,
    ruleset: int,
    cycle_states: int,
    smoothing: float,
    start_angle: int,
    max_angle: int,
    max_step: int,
) -> dict:
    """Return the next unit_id -> angle dict (floats), rate-limited to
    at most max_step degrees of change from `angles`. See the module
    docstring's Rendering model."""
    raw = _raw_levels(state, ruleset, cycle_states)

    new_angles = {}
    for uid in ALL_UNITS:
        if smoothing > 0:
            pool = [raw[uid]] + [raw[nb] for nb in neighbors(uid)]
            level = (1 - smoothing) * raw[uid] + smoothing * (sum(pool) / len(pool))
        else:
            level = raw[uid]

        target = start_angle + (max_angle - start_angle) * level
        step = max(-max_step, min(max_step, target - angles[uid]))
        new_angles[uid] = angles[uid] + step

    return new_angles


def stage_generation(angles: dict, new_angles: dict) -> bool:
    """Stage an absolute move for every unit whose angle changed by at
    least 1 degree. Does not fire; caller issues orchestrator.go_all().
    Returns True if anything was staged."""
    fired = False
    for uid in ALL_UNITS:
        if abs(new_angles[uid] - angles[uid]) >= 1.0:
            orchestrator.absolute(uid, int(round(new_angles[uid])) % 360)
            fired = True
    return fired


def run(
    stop_event,
    seed: int = 1,
    ruleset: int = 0,
    gen_ms: int = 900,
    max_step: int = 45,
    smoothing: float = 0.4,
    spawn_rate: float = 0.02,
    density: float = 0.35,
    max_angle: int = 180,
    start_angle: int = 0,
    cycle_states: int = 6,
) -> None:
    """Continuous cellular automaton; runs until stop_event is set.

    Usage (via server/modes/runner.py, not called directly):
        runner.start("life", life.run, {"ruleset": 0, "gen_ms": 900, ...})
    """
    validate(seed, ruleset, gen_ms, max_step, smoothing, spawn_rate, density, max_angle, start_angle, cycle_states)

    settings.stage_speed()
    orchestrator.absolute(orchestrator.BROADCAST, start_angle)
    orchestrator.go_all()
    if stop_event.wait(settle_ms() / 1000):
        return

    rand = rand_stream(seed)
    state = seed_board(rand, ruleset, density, cycle_states)
    angles = {uid: float(start_angle) for uid in ALL_UNITS}
    history: collections.deque = collections.deque(maxlen=_STAGNATION_HISTORY)

    origin = time.monotonic()
    next_gen_ms = 0.0

    while not stop_event.is_set():
        new_angles = render(state, angles, ruleset, cycle_states, smoothing, start_angle, max_angle, max_step)
        if stage_generation(angles, new_angles):
            orchestrator.go_all()
        angles = new_angles

        if ruleset == 2:
            state = advance(state, ruleset, rand, spawn_rate, cycle_states)
        else:
            population = sum(state.values())
            signature = frozenset(uid for uid, alive in state.items() if alive)
            if population == 0 or signature in history:
                state = seed_board(rand, ruleset, density, cycle_states)
                history.clear()
            else:
                history.append(signature)
                state = advance(state, ruleset, rand, spawn_rate, cycle_states)

        next_gen_ms += gen_ms
        now_ms = (time.monotonic() - origin) * 1000
        remaining_s = max(0.0, (next_gen_ms - now_ms) / 1000)
        if stop_event.wait(remaining_s):
            break
