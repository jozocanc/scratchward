"""Strokes-gained engine — pure logic, no CLI or DB.

Strokes gained for a single shot is how much better (or worse) the shot
left you versus the baseline expectation, after paying for the stroke(s)
you used:

    SG = E(before) - E(after) - (1 + penalty_strokes)

where E(...) is the expected strokes to hole out from a position
(distance + lie), looked up from the built-in baseline. A holed shot has
E(after) = 0. Positive SG means you gained on the field; negative means
you lost.

Each shot is also attributed to one category — off-the-tee, approach,
short-game, or putting — based on its STARTING position (Broadie's
convention). Par is only needed to tell a par-3 tee shot (an approach)
from a par-4/5 tee shot (off-the-tee); when par is unknown, tee shots
default to off-the-tee.
"""

from __future__ import annotations

from .data.sg_baseline import expected_strokes

# Shots starting this close to the pin (in yards, off the green) are
# "around the green" — short game rather than approach.
SHORT_GAME_MAX_YARDS = 30.0


def classify(start_lie: str, start_distance: float, par: int | None = None) -> str:
    """Attribute a shot to an SG category from its starting position."""
    if start_lie == "green":
        return "putting"
    if start_lie == "tee":
        return "approach" if par == 3 else "off-the-tee"
    if start_distance is not None and start_distance <= SHORT_GAME_MAX_YARDS:
        return "short-game"
    return "approach"


def strokes_gained(
    start_distance: float,
    start_lie: str,
    *,
    end_distance: float | None = None,
    end_lie: str | None = None,
    holed: bool = False,
    penalty: int = 0,
) -> float:
    """Strokes gained for one shot. Pure — only touches the baseline table.

    On the green, distances are feet; elsewhere yards (the baseline table
    follows the same convention). `penalty` counts penalty strokes taken
    on this shot (e.g. 1 for a water ball).
    """
    e_before = expected_strokes(start_distance, start_lie)
    if holed:
        e_after = 0.0
    else:
        if end_distance is None or end_lie is None:
            raise ValueError("non-holed shot needs end_distance and end_lie")
        e_after = expected_strokes(end_distance, end_lie)
    return round(e_before - e_after - (1 + penalty), 3)
