"""Built-in expected-strokes (strokes-gained) baseline table.

Values approximate PGA Tour / scratch expected strokes to hole out from a
given distance and lie. They are the reference the strokes-gained engine
compares each shot against. Distances are in yards except putting, which
is in feet.

This is the scaffold the SG phase will consume via ``expected_strokes``.
The table is intentionally a small anchor set; ``expected_strokes``
interpolates between anchors so any distance resolves. Numbers are
rounded, public-domain-style benchmarks (Broadie-style); tune freely.
"""

from __future__ import annotations

from bisect import bisect_left

# Putting: distance in FEET -> expected putts.
PUTTING = {
    1: 1.001,
    2: 1.009,
    3: 1.053,
    4: 1.147,
    5: 1.256,
    6: 1.357,
    7: 1.443,
    8: 1.515,
    9: 1.575,
    10: 1.626,
    15: 1.785,
    20: 1.873,
    30: 1.985,
    40: 2.064,
    50: 2.127,
    60: 2.182,
}

# Approach/short-game from various lies: distance in YARDS -> expected strokes.
FAIRWAY = {
    10: 1.95,
    20: 2.21,
    30: 2.40,
    40: 2.59,
    50: 2.68,
    60: 2.74,
    70: 2.78,
    80: 2.82,
    90: 2.86,
    100: 2.92,
    120: 2.99,
    140: 2.97,
    160: 3.03,
    180: 3.16,
    200: 3.31,
    220: 3.45,
}

ROUGH = {
    10: 2.18,
    20: 2.40,
    30: 2.59,
    40: 2.78,
    50: 2.87,
    60: 2.91,
    70: 2.96,
    80: 3.02,
    90: 3.08,
    100: 3.15,
    120: 3.23,
    140: 3.29,
    160: 3.40,
    180: 3.55,
    200: 3.70,
    220: 3.84,
}

SAND = {
    10: 2.43,
    20: 2.59,
    30: 2.75,
    40: 2.89,
    50: 2.96,
    60: 3.02,
    70: 3.08,
    80: 3.14,
    90: 3.20,
    100: 3.27,
    120: 3.36,
    140: 3.45,
    160: 3.58,
    180: 3.73,
    200: 3.88,
}

# Tee shots on par-4/par-5 holes: distance in YARDS -> expected strokes.
TEE = {
    100: 2.92,
    150: 3.05,
    200: 3.31,
    250: 3.50,
    300: 3.68,
    350: 3.84,
    400: 3.99,
    450: 4.12,
    500: 4.26,
    550: 4.40,
    600: 4.54,
}

_TABLES = {
    "tee": TEE,
    "fairway": FAIRWAY,
    "rough": ROUGH,
    "sand": SAND,
    "recovery": ROUGH,  # treat recovery like rough until a dedicated table exists
    "green": PUTTING,
}


def expected_strokes(distance: float, lie: str) -> float:
    """Expected strokes to hole out from `distance` (yards; feet if green).

    Linearly interpolates between the nearest anchor points, clamping to
    the table's range at the extremes.
    """
    table = _TABLES.get(lie)
    if table is None:
        raise ValueError(f"unknown lie {lie!r}")
    xs = sorted(table)
    if distance <= xs[0]:
        return table[xs[0]]
    if distance >= xs[-1]:
        return table[xs[-1]]
    i = bisect_left(xs, distance)
    if xs[i] == distance:
        return table[distance]
    lo, hi = xs[i - 1], xs[i]
    frac = (distance - lo) / (hi - lo)
    return table[lo] + frac * (table[hi] - table[lo])
