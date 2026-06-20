"""``round`` — log rounds and list history. Fully implemented.

A "round" is the unit the handicap calculation consumes: a score against
a course of known rating and slope, on a date. Shot-level detail (for
strokes gained) is logged separately and can reference a round by id.
"""

from __future__ import annotations

import argparse
from datetime import date as date_cls, datetime

from .. import db


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("round", help="Log and review rounds")
    sub = p.add_subparsers(dest="round_command", metavar="<subcommand>")
    sub.required = True

    add = sub.add_parser("add", help="Log a round")
    add.add_argument("--score", type=int, required=True, help="Total strokes")
    add.add_argument(
        "--rating", type=float, required=True, help="Course rating (e.g. 71.2)"
    )
    add.add_argument("--slope", type=int, required=True, help="Slope rating (55-155)")
    add.add_argument(
        "--date",
        default=None,
        help="Round date YYYY-MM-DD (default: today)",
    )
    add.add_argument("--course", default=None, help="Course name (optional)")
    add.add_argument(
        "--holes",
        type=int,
        default=18,
        choices=(9, 18),
        help="Holes played (default 18)",
    )
    add.set_defaults(func=run_add)

    lst = sub.add_parser("list", help="Show round history (newest first)")
    lst.add_argument(
        "--limit", type=int, default=20, help="Max rounds to show (default 20)"
    )
    lst.set_defaults(func=run_list)


def _parse_date(value: str | None) -> str:
    if value is None:
        return date_cls.today().isoformat()
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except ValueError:
        raise SystemExit(f"error: --date must be YYYY-MM-DD, got {value!r}")


def _differential(score: int, rating: float, slope: int) -> float:
    """WHS score differential for a single round."""
    return (113.0 / slope) * (score - rating)


def run_add(args: argparse.Namespace) -> int:
    if not (55 <= args.slope <= 155):
        raise SystemExit("error: --slope must be between 55 and 155")
    iso_date = _parse_date(args.date)

    conn = db.connect(args.db)
    with conn:
        cur = conn.execute(
            "INSERT INTO rounds (date, course, score, course_rating, slope, holes) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (iso_date, args.course, args.score, args.rating, args.slope, args.holes),
        )
    diff = _differential(args.score, args.rating, args.slope)
    course = f" at {args.course}" if args.course else ""
    print(
        f"Logged round #{cur.lastrowid}{course} on {iso_date}: "
        f"{args.score} ({args.holes} holes), "
        f"rating {args.rating}, slope {args.slope}."
    )
    print(f"Score differential: {diff:+.1f}")
    return 0


def run_list(args: argparse.Namespace) -> int:
    conn = db.connect(args.db)
    rows = conn.execute(
        "SELECT * FROM rounds ORDER BY date DESC, id DESC LIMIT ?",
        (args.limit,),
    ).fetchall()
    if not rows:
        print("No rounds logged yet. Add one with:")
        print("  python -m scratch round add --score 90 --rating 71.2 --slope 130")
        return 0

    header = f"{'ID':>3}  {'Date':<10}  {'Course':<20}  {'Score':>5}  {'Rtg':>5}  {'Slope':>5}  {'Diff':>6}"
    print(header)
    print("-" * len(header))
    for r in rows:
        diff = _differential(r["score"], r["course_rating"], r["slope"])
        course = (r["course"] or "")[:20]
        print(
            f"{r['id']:>3}  {r['date']:<10}  {course:<20}  "
            f"{r['score']:>5}  {r['course_rating']:>5.1f}  {r['slope']:>5}  {diff:>+6.1f}"
        )
    print(f"\n{len(rows)} round(s).")
    return 0
