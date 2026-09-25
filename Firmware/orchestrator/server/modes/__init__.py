"""
server/modes/__init__.py — Mode registry.

Modes are the set-and-forget counterpart to server/patterns/: instead of a
one-off choreography burst staged into unit queues and fired once, a mode
runs continuously — driven by an orchestrator-side background thread (see
runner.py) — until POST /api/modes/stop (or any pattern run, or the top
bar's Stop button) stops it. See runner.py's module docstring for why
long-running choreography needs orchestrator-side timing rather than
unit-side W timers.

REGISTRY maps a mode id to its run(stop_event, **kwargs) entry point.
MODE_META is the parallel metadata list consumed by GET /api/modes and
rendered by src/pages/ModesPage.jsx — same {id, label, description,
params[]} shape server/patterns/__init__.py's PATTERN_META uses.

Adding a new mode: create server/modes/<name>.py with a
run(stop_event, **kwargs) entry point that polls stop_event (via
stop_event.wait(seconds), never time.sleep) rather than running to
completion, then add it to REGISTRY and MODE_META below.
"""

from server import settings
from server.modes import auto, life, ripple, shimmer, wave

REGISTRY: dict = {
    "wave": wave.run,
    "random": shimmer.run,
    "life": life.run,
    "ripple": ripple.run,
    "auto": auto.run,
}

VALIDATORS: dict = {
    "wave": wave.validate,
    "random": shimmer.validate,
    "life": life.validate,
    "ripple": ripple.validate,
    "auto": auto.validate,
}

MODE_META: list = [
    {
        "id": "wave",
        "label": "Wave",
        "description": "A travelling wave sweeps continuously edge to edge, repeating until stopped.",
        "params": [
            {
                "key": "angle_deg",
                "label": "Direction",
                "type": "int",
                "default": 0,
                "min": 0,
                "max": 330,
                "step": 30,
            },
            {
                "key": "sweep_ms",
                "label": "Sweep Time (ms)",
                "type": "int",
                "default": 5500,
                "min": wave.min_sweep_ms(),
                "max": 20000,
                "step": 100,
            },
            {
                "key": "amplitude",
                "label": "Amplitude (deg)",
                "type": "int",
                "default": 180,
                "min": 10,
                "max": 180,
                "step": 10,
            },
            {
                "key": "start_angle",
                "label": "Start Angle (deg)",
                "type": "int",
                "default": 0,
                "min": 0,
                "max": 170,
                "step": 10,
            },
            {
                "key": "angle_step_deg",
                "label": "Rotation per Iteration (deg)",
                "type": "int",
                "default": 5,
                "min": -45,
                "max": 45,
                "step": 1,
            },
        ],
    },
    {
        "id": "random",
        "label": "Random",
        "description": "Each unit bounces in and out by a random amount at random times, continuing until stopped.",
        "params": [
            {
                "key": "seed",
                "label": "Seed",
                "type": "int",
                "default": 1,
                "min": 0,
                "max": 9999,
                "step": 1,
            },
            {
                "key": "interval_ms",
                "label": "Roll Interval (ms)",
                "type": "int",
                "default": 5000,
                "min": shimmer.min_interval_ms(),
                "max": 20000,
                "step": 250,
            },
            {
                "key": "density",
                "label": "Density",
                "type": "float",
                "default": 0.8,
                "min": 0.05,
                "max": 1.0,
                "step": 0.05,
            },
            {
                "key": "min_amplitude",
                "label": "Min Amplitude (deg)",
                "type": "int",
                "default": 120,
                "min": 10,
                "max": 180,
                "step": 10,
            },
            {
                "key": "max_amplitude",
                "label": "Max Amplitude (deg)",
                "type": "int",
                "default": 180,
                "min": 10,
                "max": 180,
                "step": 10,
            },
            {
                "key": "start_angle",
                "label": "Start Angle (deg)",
                "type": "int",
                "default": 0,
                "min": 0,
                "max": 170,
                "step": 10,
            },
        ],
    },
    {
        "id": "life",
        "label": "Life",
        "description": "A cellular automaton ripples across the honeycomb, continuing until stopped.",
        "params": [
            {
                "key": "seed",
                "label": "Seed",
                "type": "int",
                "default": 1,
                "min": 0,
                "max": 9999,
                "step": 1,
            },
            {
                "key": "ruleset",
                "label": "Ruleset (0=B2/S34, 1=B2/S23, 2=Cyclic)",
                "type": "int",
                "default": 0,
                "min": 0,
                "max": 2,
                "step": 1,
            },
            {
                "key": "gen_ms",
                "label": "Generation (ms)",
                "type": "int",
                "default": 900,
                "min": life.min_gen_ms(),
                "max": 6000,
                "step": 50,
            },
            {
                "key": "max_step",
                "label": "Max Step (deg)",
                "type": "int",
                "default": 45,
                "min": 10,
                "max": 180,
                "step": 5,
            },
            {
                "key": "smoothing",
                "label": "Smoothing",
                "type": "float",
                "default": 0.4,
                "min": 0.0,
                "max": 1.0,
                "step": 0.05,
            },
            {
                "key": "spawn_rate",
                "label": "Spawn Rate (rulesets 0/1 only)",
                "type": "float",
                "default": 0.02,
                "min": 0.0,
                "max": 0.2,
                "step": 0.01,
            },
            {
                "key": "density",
                "label": "Seed Density (rulesets 0/1 only)",
                "type": "float",
                "default": 0.35,
                "min": 0.05,
                "max": 1.0,
                "step": 0.05,
            },
            {
                "key": "max_angle",
                "label": "Max Angle (deg)",
                "type": "int",
                "default": 180,
                "min": 30,
                "max": 180,
                "step": 10,
            },
            {
                "key": "start_angle",
                "label": "Start Angle (deg)",
                "type": "int",
                "default": 0,
                "min": 0,
                "max": 150,
                "step": 10,
            },
            {
                "key": "cycle_states",
                "label": "Cycle States (ruleset 2 only)",
                "type": "int",
                "default": 6,
                "min": 3,
                "max": 12,
                "step": 1,
            },
        ],
    },
    {
        "id": "ripple",
        "label": "Ripple",
        "description": "Concentric rings radiate from the centre, repeating until stopped.",
        "params": [
            {
                "key": "period_ms",
                "label": "Period (ms)",
                "type": "int",
                "default": 5000,
                "min": ripple.min_period_ms(),
                "max": 20000,
                "step": 100,
            },
            {
                "key": "spread_ms",
                "label": "Spread Time (ms)",
                "type": "int",
                "default": 3000,
                "min": 0,
                "max": 20000,
                "step": 100,
            },
            {
                "key": "amplitude",
                "label": "Amplitude (deg)",
                "type": "int",
                "default": 150,
                "min": 10,
                "max": 180,
                "step": 10,
            },
            {
                "key": "start_angle",
                "label": "Start Angle (deg)",
                "type": "int",
                "default": 0,
                "min": 0,
                "max": 170,
                "step": 10,
            },
            {
                "key": "direction",
                "label": "Direction (0=Outward, 1=Inward)",
                "type": "int",
                "default": 0,
                "min": 0,
                "max": 1,
                "step": 1,
            },
        ],
    },
    {
        "id": "auto",
        "label": "Auto",
        "description": "Cycles through wave, ripple, and random forever, crossfading between them — "
        "for unattended operation. Tune the rotation in server/modes/auto_config.py, not here.",
        "params": [],
    },
]

# id -> (param key, floor function) for the four time-valued params above
# whose `min` is derived from move_ms()/one_sided_bounce_ms() at import
# time — which only ever runs at settings.REFERENCE_RPM (14.5), since
# nothing has loaded the persisted default_rpm yet — and whose fixed
# `default` (5500/5000/900/5000 above) was authored at that same speed.
# mode_meta() below recomputes both against whatever speed is actually
# configured right now.
_SPEED_DEPENDENT_PARAMS = {
    "wave": ("sweep_ms", wave.min_sweep_ms),
    "random": ("interval_ms", shimmer.min_interval_ms),
    "life": ("gen_ms", life.min_gen_ms),
    "ripple": ("period_ms", ripple.min_period_ms),
}


def resolve_params(name: str, payload: dict) -> dict:
    """Fill in a mode's defaults from mode_meta(), reject unknown keys,
    coerce each provided value by its declared type, and run the mode's
    own VALIDATORS entry (if any). Raises ValueError on any problem, with
    a message fit to hand straight back to an HTTP client.

    Extracted from what used to be inline in POST /api/modes/<name>
    (server/routes/modes.py) so server/scheduler.py's replay of the
    last-used mode at the daily "on" transition goes through the exact
    same queue-safety guard the HTTP path enforces — the two must not be
    able to drift, since a replay that skipped this could overflow a
    unit's queue with no operator watching to notice on a one-way bus.
    """
    meta = {m["id"]: m for m in mode_meta()}.get(name)
    if meta is None:
        raise ValueError(f"unknown mode: {name}")
    param_specs = {p["key"]: p for p in meta["params"]}

    if not isinstance(payload, dict):
        payload = {}

    unknown = set(payload) - set(param_specs)
    if unknown:
        raise ValueError(f"unknown parameter(s) for {name}: {sorted(unknown)}")

    kwargs = {spec["key"]: spec["default"] for spec in meta["params"]}
    for key, raw in payload.items():
        spec = param_specs[key]
        try:
            kwargs[key] = float(raw) if spec["type"] == "float" else int(raw)
        except (TypeError, ValueError):
            raise ValueError(f"parameter {key!r} must be a {spec['type']}, got {raw!r}") from None

    validator = VALIDATORS.get(name)
    if validator is not None:
        validator(**kwargs)  # raises ValueError on its own

    return kwargs


def mode_meta() -> list:
    """Return a fresh copy of MODE_META with the four speed-dependent
    params' `min` *and* `default` recomputed against the currently
    configured default_rpm (see server/settings.py and
    _SPEED_DEPENDENT_PARAMS above).

    Recomputing `default` isn't just cosmetic: MODE_META's fixed default
    (e.g. wave's sweep_ms=6000) is only ever valid at settings.REFERENCE_RPM
    (14.5) — at a slower configured speed it can fall *below* the also-
    rising floor (one_sided_bounce_ms(180) alone exceeds 6000ms below
    ~11.4 RPM), so a mode started at its on-page defaults would 400.
    Scaling it by settings.speed_scale() — the same transform
    server/modes/auto.py's _scaled_params() applies to auto_config.py's
    segments — keeps a mode's default choreography valid (and its relative
    timing shape intact) at any configured speed; clamped to [floor, max]
    as a defensive bound, though every param's authored max already leaves
    enough headroom across settings.MIN_RPM..MAX_RPM's whole range.

    GET /api/modes, POST /api/modes/<name>, and simhost/selftest.py's
    check_modes() all call this instead of reading MODE_META directly.
    """
    import copy

    meta = copy.deepcopy(MODE_META)
    scale = settings.speed_scale()
    for entry in meta:
        floor = _SPEED_DEPENDENT_PARAMS.get(entry["id"])
        if floor is None:
            continue
        key, fn = floor
        for param in entry["params"]:
            if param["key"] != key:
                continue
            floor_ms = fn()
            param["min"] = floor_ms
            scaled_default = round(param["default"] * scale)
            param["default"] = max(floor_ms, min(param["max"], scaled_default))
    return meta
