"""``handicap`` — compute the Handicap Index. Fully implemented.

Method (per the project spec, blending the WHS differential-selection
table with the requested 0.96 multiplier):

    1. differential = (113 / slope) * (score - course_rating) for each round
    2. take the most recent 20 rounds
    3. from those, select the lowest N differentials, where N and an
       adjustment come from the WHS table below (handles <20 rounds)
    4. index = mean(selected) * 0.96 + adjustment, rounded to 1 decimal,
       capped at the WHS maximum of 54.0

The selection table and the 0.96 factor are both isolated in this module
so the formula is trivial to tweak later.
"""

from __future__ import annotations

import argparse

from .. import db

# WHS minimum is 3 rounds (54 holes).
MIN_ROUNDS = 3
MAX_INDEX = 54.0
BONUS_FOR_EXCELLENCE = 0.96

# rounds_available -> (count_of_lowest_to_use, adjustment)
# Current World Handicap System table.
WHS_TABLE = {
    3: (1, -2.0),
    4: (1, -1.0),
    5: (1, 0.0),
    6: (2, -1.0),
    7: (2, 0.0),
    8: (2, 0.0),
    9: (3, 0.0),
    10: (3, 0.0),
    11: (3, 0.0),
    12: (4, 0.0),
    13: (4, 0.0),
    14: (4, 0.0),
    15: (5, 0.0),
    16: (5, 0.0),
    17: (6, 0.0),
    18: (6, 0.0),
    19: (7, 0.0),
    20: (8, 0.0),
}


def _selection(n_rounds: int) -> tuple[int, float]:
    """Return (lowest_count, adjustment) for n_rounds (capped at 20)."""
    return WHS_TABLE[min(n_rounds, 20)]


def compute_handicap_index(differentials: list[float]) -> dict | None:
    """Compute the index from a list of differentials (most-recent-first).

    Returns a dict with the index and the working numbers, or None if
    there aren't enough rounds. Pure function — no DB access, so it's
    easy to unit-test.
    """
    if len(differentials) < MIN_ROUNDS:
        return None

    recent = differentials[:20]
    count, adjustment = _selection(len(recent))
    lowest = sorted(recent)[:count]
    mean_low = sum(lowest) / len(lowest)
    index = round(mean_low * BONUS_FOR_EXCELLENCE + adjustment, 1)
    index = min(index, MAX_INDEX)
    return {
        "index": index,
        "rounds_used": len(recent),
        "lowest_count": count,
        "adjustment": adjustment,
        "lowest_differentials": lowest,
        "mean_lowest": round(mean_low, 2),
    }


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("handicap", help="Compute your Handicap Index")
    p.add_argument(
        "--verbose",
        action="store_true",
        help="Show the differentials and table selection used",
    )
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    conn = db.connect(args.db)
    rows = conn.execute(
        "SELECT score, course_rating, slope FROM rounds "
        "ORDER BY date DESC, id DESC LIMIT 20"
    ).fetchall()

    differentials = [
        (113.0 / r["slope"]) * (r["score"] - r["course_rating"]) for r in rows
    ]
    result = compute_handicap_index(differentials)

    if result is None:
        have = len(differentials)
        print(
            f"Need at least {MIN_ROUNDS} rounds to compute a Handicap Index "
            f"(you have {have})."
        )
        print("Log more with: python -m scratch round add ...")
        return 0

    print(f"Handicap Index: {result['index']:.1f}")
    print(
        f"  based on the lowest {result['lowest_count']} of your "
        f"{result['rounds_used']} most recent rounds"
    )
    if result["adjustment"]:
        print(f"  WHS adjustment applied: {result['adjustment']:+.1f}")

    if args.verbose:
        print(f"\n  mean of lowest differentials: {result['mean_lowest']:.2f}")
        print(f"  x {BONUS_FOR_EXCELLENCE} bonus for excellence")
        used = ", ".join(f"{d:+.1f}" for d in result["lowest_differentials"])
        print(f"  differentials used: {used}")
        all_diffs = ", ".join(f"{d:+.1f}" for d in sorted(differentials))
        print(f"  all differentials (sorted): {all_diffs}")
    return 0
