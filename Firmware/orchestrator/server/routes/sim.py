from flask import jsonify, request

import tap
from server.patterns.layout import RING_START, RING_WINDING, RINGS, TILE_ORIENTATION, axial


def get_sim_tail():
    """GET /api/sim/tail?since=N — commands recorded after seq N.

    Powers the simulator: it polls this endpoint and replays whatever
    orchestrator.send() has written to the tap since its last poll.
    Omit `since` (or pass a value before the buffer's oldest entry) to
    get everything currently held.
    """
    # type=int falls back to the default on a missing OR unparseable value.
    since = request.args.get("since", -1, type=int)
    return jsonify(tap.tail(since))


def get_layout():
    """GET /api/layout — physical topology for the simulator's renderer.

    One source of truth: coordinates are computed in
    server/patterns/layout.py, not duplicated in JS.
    """
    units = []
    for ring_idx, ring in enumerate(RINGS):
        for uid in ring:
            q, r = axial(uid)
            units.append({"id": uid, "ring": ring_idx, "q": q, "r": r})
    return jsonify(
        {
            "tile_orientation": TILE_ORIENTATION,
            "ring_start": RING_START,
            "ring_winding": RING_WINDING,
            "units": units,
        }
    )
