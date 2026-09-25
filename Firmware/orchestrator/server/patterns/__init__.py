"""
server/patterns — Pattern registry for the hexagon orchestrator.

Each pattern is a module with a ``run(**kwargs)`` entry-point.
The REGISTRY maps URL-safe names to their run functions so that the
route handler can dispatch without knowing about individual modules.

Adding a new pattern
--------------------
1. Create ``server/patterns/<your_pattern>.py`` with a ``run(**kwargs)`` function.
2. Import it here and add an entry to REGISTRY.
"""

from server.patterns import bloom, cascade, home_wave, pinwheel, rain, sparkle, sweep, water_drop

# ---------------------------------------------------------------------------
# Pattern registry
#
# Keys   : URL-safe names used in /api/patterns/<name>
# Values : callable run(**kwargs) functions
#
# home_wave is registered here (so POST /api/patterns/home_wave works, used
# by the top bar's Home button) but deliberately left out of PATTERN_META
# below — it's a system operation, not a choreography effect, and shouldn't
# show up as a card on the Patterns page.
# ---------------------------------------------------------------------------
REGISTRY: dict = {
    "water_drop": water_drop.run,
    "cascade": cascade.run,
    "sweep": sweep.run,
    "sparkle": sparkle.run,
    "pinwheel": pinwheel.run,
    "bloom": bloom.run,
    "rain": rain.run,
    "home_wave": home_wave.run,
}

# Human-readable metadata for the UI.
# Each entry may include a ``params`` list describing configurable kwargs.
# Param fields: key, label, type ("int"|"float"), default, min, max, step.
PATTERN_META: list[dict] = [
    {
        "id": "water_drop",
        "label": "Water Drop",
        "description": "A ripple radiates outward from the centre with decaying amplitude.",
        "params": [
            {"key": "cycles", "label": "Cycles", "type": "int", "default": 4, "min": 1, "max": 10, "step": 1},
            {
                "key": "start_amplitude",
                "label": "Start Amplitude (°)",
                "type": "int",
                "default": 180,
                "min": 10,
                "max": 180,
                "step": 10,
            },
            {
                "key": "decay_factor",
                "label": "Decay Factor",
                "type": "float",
                "default": 0.667,
                "min": 0.1,
                "max": 0.9,
                "step": 0.05,
            },
            {
                "key": "ring_delay_ms",
                "label": "Ring Delay (ms)",
                "type": "int",
                "default": 600,
                "min": 0,
                "max": 2000,
                "step": 50,
            },
        ],
    },
    {
        "id": "cascade",
        "label": "Cascade",
        "description": "Units bob one after another in wiring order, each starting slightly after the last.",
        "params": [
            {"key": "cycles", "label": "Cycles", "type": "int", "default": 3, "min": 1, "max": 10, "step": 1},
            {
                "key": "start_amplitude",
                "label": "Start Amplitude (\u00b0)",
                "type": "int",
                "default": 180,
                "min": 10,
                "max": 180,
                "step": 10,
            },
            {
                "key": "decay_factor",
                "label": "Decay Factor",
                "type": "float",
                "default": 0.667,
                "min": 0.1,
                "max": 0.9,
                "step": 0.05,
            },
            {
                "key": "unit_delay_ms",
                "label": "Unit Delay (ms)",
                "type": "int",
                "default": 400,
                "min": 0,
                "max": 2000,
                "step": 50,
            },
        ],
    },
    {
        "id": "sweep",
        "label": "Sweep",
        "description": "A wave flows across the piece in a straight line, e.g. corner to corner.",
        "params": [
            {
                "key": "direction_deg",
                "label": "Direction (°)",
                "type": "int",
                "default": 60,
                "min": 0,
                "max": 330,
                "step": 30,
            },
            {
                "key": "sweep_ms",
                "label": "Sweep Time (ms)",
                "type": "int",
                "default": 4000,
                "min": 0,
                "max": 8000,
                "step": 100,
            },
            {"key": "cycles", "label": "Cycles", "type": "int", "default": 3, "min": 1, "max": 10, "step": 1},
            {
                "key": "start_amplitude",
                "label": "Start Amplitude (°)",
                "type": "int",
                "default": 180,
                "min": 10,
                "max": 180,
                "step": 10,
            },
            {
                "key": "decay_factor",
                "label": "Decay Factor",
                "type": "float",
                "default": 0.75,
                "min": 0.1,
                "max": 0.9,
                "step": 0.05,
            },
        ],
    },
    {
        "id": "sparkle",
        "label": "Sparkle",
        "description": "Units shimmer independently at random times and amplitudes from a seeded PRNG.",
        "params": [
            {"key": "seed", "label": "Seed", "type": "int", "default": 1, "min": 0, "max": 9999, "step": 1},
            {
                "key": "spread_ms",
                "label": "Spread (ms)",
                "type": "int",
                "default": 3000,
                "min": 0,
                "max": 10000,
                "step": 100,
            },
            {
                "key": "density",
                "label": "Density",
                "type": "float",
                "default": 0.6,
                "min": 0.1,
                "max": 1.0,
                "step": 0.05,
            },
            {
                "key": "amp_jitter",
                "label": "Amplitude Jitter",
                "type": "float",
                "default": 0.4,
                "min": 0.1,
                "max": 1.0,
                "step": 0.05,
            },
            {"key": "cycles", "label": "Cycles", "type": "int", "default": 2, "min": 1, "max": 10, "step": 1},
            {
                "key": "start_amplitude",
                "label": "Start Amplitude (°)",
                "type": "int",
                "default": 150,
                "min": 10,
                "max": 180,
                "step": 10,
            },
            {
                "key": "decay_factor",
                "label": "Decay Factor",
                "type": "float",
                "default": 0.6,
                "min": 0.1,
                "max": 0.9,
                "step": 0.05,
            },
        ],
    },
    {
        "id": "pinwheel",
        "label": "Pinwheel",
        "description": "A wave rotates around the centre like a radar sweep or spinning propeller.",
        "params": [
            {"key": "arms", "label": "Arms", "type": "int", "default": 2, "min": 1, "max": 6, "step": 1},
            {
                "key": "rotation_ms",
                "label": "Rotation Time (ms)",
                "type": "int",
                "default": 4400,
                "min": 500,
                "max": 10000,
                "step": 100,
            },
            {
                "key": "spiral_pitch",
                "label": "Spiral Pitch",
                "type": "float",
                "default": 0.0,
                "min": -2.0,
                "max": 2.0,
                "step": 0.1,
            },
            {"key": "cycles", "label": "Cycles", "type": "int", "default": 3, "min": 1, "max": 10, "step": 1},
            {
                "key": "start_amplitude",
                "label": "Start Amplitude (°)",
                "type": "int",
                "default": 180,
                "min": 10,
                "max": 180,
                "step": 10,
            },
            {
                "key": "decay_factor",
                "label": "Decay Factor",
                "type": "float",
                "default": 0.8,
                "min": 0.1,
                "max": 0.9,
                "step": 0.05,
            },
        ],
    },
    {
        "id": "bloom",
        "label": "Bloom",
        "description": "The whole piece breathes as a dome (or bowl), amplitude grading from centre to edge.",
        "params": [
            {
                "key": "centre_amplitude",
                "label": "Centre Amplitude (°)",
                "type": "int",
                "default": 180,
                "min": 10,
                "max": 180,
                "step": 10,
            },
            {
                "key": "edge_amplitude",
                "label": "Edge Amplitude (°)",
                "type": "int",
                "default": 60,
                "min": 10,
                "max": 180,
                "step": 10,
            },
            {
                "key": "ring_lead_ms",
                "label": "Ring Lead (ms)",
                "type": "int",
                "default": 150,
                "min": 0,
                "max": 1000,
                "step": 25,
            },
            {"key": "cycles", "label": "Cycles", "type": "int", "default": 4, "min": 1, "max": 10, "step": 1},
            {
                "key": "decay_factor",
                "label": "Decay Factor",
                "type": "float",
                "default": 0.85,
                "min": 0.1,
                "max": 0.9,
                "step": 0.05,
            },
        ],
    },
    {
        "id": "rain",
        "label": "Rain",
        "description": "Random impacts land across the piece, each spawning its own small ripple.",
        "params": [
            {"key": "seed", "label": "Seed", "type": "int", "default": 1, "min": 0, "max": 9999, "step": 1},
            {"key": "drops", "label": "Drops", "type": "int", "default": 5, "min": 1, "max": 8, "step": 1},
            {
                "key": "spread_ms",
                "label": "Spread (ms)",
                "type": "int",
                "default": 6000,
                "min": 0,
                "max": 15000,
                "step": 250,
            },
            {
                "key": "ripple_rings",
                "label": "Ripple Rings",
                "type": "int",
                "default": 3,
                "min": 1,
                "max": 5,
                "step": 1,
            },
            {
                "key": "ripple_delay_ms",
                "label": "Ripple Delay (ms)",
                "type": "int",
                "default": 400,
                "min": 50,
                "max": 1200,
                "step": 50,
            },
            {
                "key": "start_amplitude",
                "label": "Start Amplitude (°)",
                "type": "int",
                "default": 180,
                "min": 10,
                "max": 180,
                "step": 10,
            },
            {
                "key": "distance_decay",
                "label": "Distance Decay",
                "type": "float",
                "default": 0.7,
                "min": 0.1,
                "max": 0.9,
                "step": 0.05,
            },
        ],
    },
]
