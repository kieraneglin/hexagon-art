"""
server/patterns/layout.py — Physical layout of hexagon units.

Defines which unit IDs belong to which ring (concentric hexagonal rings).
Ring 0 is the centre; ring 1 is the first surrounding layer; and so on.

Editing this file is the ONLY thing needed when adding more units — pattern
code iterates over RINGS and never hard-codes IDs directly.

Current hardware (61-unit full installation, 5 rings deep):
  - Unit 01 : centre (ring 0)
  - Units 02-07 : ring 1 (6 units)
  - Units 08-13 : ring 2 (12 units)
  - Units 14-25 : ring 3 (18 units) [0x14-0x25]
  - Units 26-61 : ring 4 (24 units) [0x26-0x3D]

Orientation: pointy-top tiles (vertices at north/south, flat edges at
east/west) — see the geometry section below.

As of 2026-08-24 the physical piece was rotated 60 degrees counter-clockwise
on the wall. Within each ring, unit IDs are assigned clockwise starting from
due NORTH-EAST (previously due east — see RING_START below).

Unit IDs are integers (0x01-0xFE).  The broadcast address 0xFF is reserved.
"""

# ---------------------------------------------------------------------------
# Ring definitions
#
# Each entry is a list of integer unit IDs in that ring.
# Ring 0 is always the centre; subsequent rings radiate outward.
# Within each ring, IDs are ordered clockwise starting from due north-east
# (see RING_START and the module docstring above).
# ---------------------------------------------------------------------------

# fmt: off
RINGS: list[list[int]] = [
    [0x01],  # ring 0 — centre
    [0x02, 0x03, 0x04, 0x05, 0x06, 0x07],  # ring 1 — 6 units
    [0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D,
     0x0E, 0x0F, 0x10, 0x11, 0x12, 0x13],  # ring 2 — 12 units
    [0x14, 0x15, 0x16, 0x17, 0x18, 0x19,
     0x1A, 0x1B, 0x1C, 0x1D, 0x1E, 0x1F,
     0x20, 0x21, 0x22, 0x23, 0x24, 0x25],  # ring 3 — 18 units
    [0x26, 0x27, 0x28, 0x29, 0x2A, 0x2B,
     0x2C, 0x2D, 0x2E, 0x2F, 0x30, 0x31,
     0x32, 0x33, 0x34, 0x35, 0x36, 0x37,
     0x38, 0x39, 0x3A, 0x3B, 0x3C, 0x3D],  # ring 4 — 24 units
]
# fmt: on

# Flat list of all unit IDs, in ring order (handy for broadcasts).
ALL_UNITS: list[int] = [uid for ring in RINGS for uid in ring]

# ---------------------------------------------------------------------------
# Geometry
#
# RINGS above only encodes ring membership and within-ring order — no
# coordinates. This section derives axial hex coordinates from it, so both
# pattern code and the simulator have one source of truth for physical
# position instead of re-deriving (or duplicating) it.
#
# Confirmed against the physical piece: each tile is POINTY-TOP (vertices
# north/south, flat edges east/west). The aggregate 5-ring outline is a
# large FLAT-TOP hexagon — that shape falls out automatically from packing
# pointy-top tiles into hex rings; it is not a separate configuration. A
# hexagon has 6-fold rotational symmetry, so a 60-degree rotation of the
# whole piece maps both shapes onto themselves — it never changes
# TILE_ORIENTATION or pixel()'s math, only which unit ID sits where.
#
# The physical wiring order (which direction ring 1 starts from, and which
# way it winds) was inferred from unit IDs, not independently verified
# against the wall — and on 2026-08-24 the piece itself was rotated 60
# degrees counter-clockwise, which is equivalent to starting each ring one
# compass step earlier (E -> NE). RING_START/RING_WINDING below are live
# inputs to _ring_walk(), not prose — if the simulator's unit-ID overlay
# ever stops matching the real piece, changing them here is sufficient.
# ---------------------------------------------------------------------------

TILE_ORIENTATION = "pointy"  # vertices N/S, flats E/W — do not change without re-deriving pixel()
RING_START = "NE"  # first unit in each ring (after the centre) sits due north-east
RING_WINDING = "CW"  # subsequent units proceed clockwise from RING_START

# Axial (q, r) unit directions for a pointy-top layout, starting due east
# and proceeding clockwise. This is the canonical compass table neighbors()
# uses for its "CW from E" contract — do not rotate it in place; _ring_walk()
# below rotates a copy to RING_START instead, so neighbors() keeps meaning
# what it says regardless of wiring order.
_DIRECTIONS_CW_FROM_E: list[tuple[int, int]] = [
    (1, 0),  # E
    (0, 1),  # SE
    (-1, 1),  # SW
    (-1, 0),  # W
    (0, -1),  # NW
    (1, -1),  # NE
]

# Compass label -> index into _DIRECTIONS_CW_FROM_E, so RING_START can name
# a starting direction without duplicating the axial vectors.
_DIRECTION_INDEX: dict[str, int] = {"E": 0, "SE": 1, "SW": 2, "W": 3, "NW": 4, "NE": 5}


def _ring_walk(radius: int) -> list[tuple[int, int]]:
    """Axial coordinates for one ring, in wiring order (from RING_START,
    proceeding RING_WINDING).

    Walks the ring's six corners, starting at RING_START and reversing
    direction if RING_WINDING is "CCW". The edge connecting corner i to
    corner i+1 is walked by stepping `radius` times in the direction *two
    positions ahead* of corner i in that same rotated list — a standard
    hex-ring identity: the edge between two corners 60 degrees apart runs
    parallel to the direction bisecting them, not either corner's own
    direction.
    """
    if radius == 0:
        return [(0, 0)]

    start = _DIRECTION_INDEX[RING_START]
    corners = _DIRECTIONS_CW_FROM_E[start:] + _DIRECTIONS_CW_FROM_E[:start]
    if RING_WINDING == "CCW":
        corners = [corners[0]] + corners[1:][::-1]

    q, r = radius * corners[0][0], radius * corners[0][1]
    coords = []
    for i in range(6):
        dq, dr = corners[(i + 2) % 6]
        for _ in range(radius):
            coords.append((q, r))
            q, r = q + dq, r + dr
    return coords


# unit ID -> (q, r) axial coordinate, built once at import.
AXIAL: dict[int, tuple[int, int]] = {}
for _ring_idx, _ring in enumerate(RINGS):
    for _uid, _coord in zip(_ring, _ring_walk(_ring_idx)):
        AXIAL[_uid] = _coord
del _ring_idx, _ring, _uid, _coord

# (q, r) -> unit ID, the inverse of AXIAL. Built immediately after AXIAL
# since neighbors() depends on it.
_UID_BY_AXIAL: dict[tuple[int, int], int] = {coord: uid for uid, coord in AXIAL.items()}


def axial(unit_id: int) -> tuple[int, int]:
    """Return the (q, r) axial coordinate for unit_id."""
    return AXIAL[unit_id]


def pixel(unit_id: int, size: float = 1.0) -> tuple[float, float]:
    """Return the (x, y) pixel-space centre for unit_id, pointy-top layout.

    `size` is the hex circumradius in whatever unit the caller wants
    (e.g. mm or px). Standard pointy-top axial-to-pixel conversion.
    """
    q, r = AXIAL[unit_id]
    x = size * (3**0.5) * (q + r / 2)
    y = size * 1.5 * r
    return (x, y)


def ring_of(unit_id: int) -> int:
    """Return which ring (0 = centre) unit_id belongs to."""
    for idx, ring in enumerate(RINGS):
        if unit_id in ring:
            return idx
    raise KeyError(unit_id)


def neighbors(unit_id: int) -> list[int]:
    """Return the unit IDs of all populated neighbouring cells, CW from E."""
    q, r = AXIAL[unit_id]
    result = []
    for dq, dr in _DIRECTIONS_CW_FROM_E:
        other = _UID_BY_AXIAL.get((q + dq, r + dr))
        if other is not None:
            result.append(other)
    return result


def distance(a: int, b: int) -> int:
    """Axial hex distance (in ring-steps) between two units."""
    qa, ra = AXIAL[a]
    qb, rb = AXIAL[b]
    dq, dr = qa - qb, ra - rb
    return (abs(dq) + abs(dr) + abs(dq + dr)) // 2
