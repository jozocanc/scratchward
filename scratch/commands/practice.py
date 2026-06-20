"""``practice`` — practice log + the feedback loop. Fully implemented.

* ``practice add``      log a session, tagged to a fault or SG category.
* ``practice list``     recent sessions.
* ``practice progress`` the loop that matters: for each SG category you've
                        practiced, compare your strokes gained in that
                        category *before* you started working on it vs
                        *since* — so you can see whether the work paid off.

Tagging a session's ``--focus`` to an SG category (off-the-tee, approach,
short-game, putting) is what lets ``progress`` tie practice to results.
A focus that's a swing-fault tag still gets logged and counted, but can't
be scored against strokes gained.
"""

from __future__ import annotations

import argparse
from datetime import date as date_cls, datetime, timedelta

from .. import db
from ..constants import SG_CATEGORIES


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("practice", help="Practice log and progress feedback")
    sub = p.add_subparsers(dest="practice_command", metavar="<subcommand>")
    sub.required = True

    add = sub.add_parser("add", help="Log a practice session")
    add.add_argument(
        "--focus",
        required=True,
        help="What you worked on — an SG category "
        "(off-the-tee/approach/short-game/putting) or a swing-fault tag",
    )
    add.add_argument("--drills", help="Drills worked on")
    add.add_argument("--duration", type=int, help="Minutes")
    add.add_argument("--notes", help="Free-text notes")
    add.add_argument("--date", default=None, help="Date YYYY-MM-DD (default today)")
    add.set_defaults(func=run_add)

    lst = sub.add_parser("list", help="Show recent practice sessions")
    lst.add_argument("--limit", type=int, default=20)
    lst.set_defaults(func=run_list)

    prog = sub.add_parser("progress", help="Did the practice move the needle?")
    prog.add_argument(
        "--focus",
        help="Limit to one focus (default: every focus you've practiced)",
    )
    prog.add_argument(
        "--window",
        type=int,
        default=None,
        help="Only compare rounds within N days each side of when you "
        "started practicing (default: all history)",
    )
    prog.set_defaults(func=run_progress)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _parse_date(value: str | None) -> str:
    if value is None:
        return date_cls.today().isoformat()
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except ValueError:
        raise SystemExit(f"error: --date must be YYYY-MM-DD, got {value!r}")


def _shift(iso: str, days: int) -> str:
    return (datetime.strptime(iso, "%Y-%m-%d").date() + timedelta(days=days)).isoformat()


def _sg_per_round(conn, category, *, date_lo=None, date_hi=None):
    """Average strokes gained per round in a category over a date range.

    `date_lo` is inclusive, `date_hi` exclusive. A "round" is a distinct
    round_id, or a distinct date when the shot wasn't linked to a round.
    Returns (sg_per_round, n_rounds) or None if there are no shots.
    """
    clauses = ["category = ?", "sg IS NOT NULL"]
    params = [category]
    if date_lo is not None:
        clauses.append("date >= ?")
        params.append(date_lo)
    if date_hi is not None:
        clauses.append("date < ?")
        params.append(date_hi)
    rows = conn.execute(
        "SELECT date, round_id, sg FROM shots WHERE " + " AND ".join(clauses), params
    ).fetchall()
    if not rows:
        return None
    total = sum(r["sg"] for r in rows)
    keys = {
        ("r", r["round_id"]) if r["round_id"] is not None else ("d", r["date"])
        for r in rows
    }
    return total / max(len(keys), 1), len(keys)


# --------------------------------------------------------------------------- #
# practice add / list
# --------------------------------------------------------------------------- #
def run_add(args: argparse.Namespace) -> int:
    iso_date = _parse_date(args.date)
    focus = args.focus.strip().lower()
    conn = db.connect(args.db)
    with conn:
        cur = conn.execute(
            "INSERT INTO practice_sessions (date, focus, drills, duration_min, notes) "
            "VALUES (?,?,?,?,?)",
            (iso_date, focus, args.drills, args.duration, args.notes),
        )
    dur = f"{args.duration} min" if args.duration else "duration not set"
    print(f"Logged practice #{cur.lastrowid} on {iso_date}: focus={focus} ({dur}).")
    if focus in SG_CATEGORIES:
        print(f"  Tagged to SG category '{focus}' — run "
              f"`python -m scratch practice progress` to track its impact.")
    else:
        print(f"  Note: '{focus}' isn't an SG category, so it won't be scored "
              f"against strokes gained. Use one of: {', '.join(SG_CATEGORIES)}.")
    return 0


def run_list(args: argparse.Namespace) -> int:
    conn = db.connect(args.db)
    rows = conn.execute(
        "SELECT * FROM practice_sessions ORDER BY date DESC, id DESC LIMIT ?",
        (args.limit,),
    ).fetchall()
    if not rows:
        print("No practice sessions yet. Add one with:")
        print("  python -m scratch practice add --focus approach "
              "--drills 'wedge ladder' --duration 45")
        return 0
    header = f"{'ID':>3}  {'Date':<10}  {'Focus':<13}  {'Min':>4}  Drills / notes"
    print(header)
    print("-" * len(header))
    for r in rows:
        dur = r["duration_min"] if r["duration_min"] is not None else ""
        detail = r["drills"] or r["notes"] or ""
        print(f"{r['id']:>3}  {r['date']:<10}  {(r['focus'] or ''):<13}  "
              f"{str(dur):>4}  {detail[:40]}")
    print(f"\n{len(rows)} session(s).")
    return 0


# --------------------------------------------------------------------------- #
# practice progress — the feedback loop
# --------------------------------------------------------------------------- #
def run_progress(args: argparse.Namespace) -> int:
    conn = db.connect(args.db)
    where = ""
    params: list = []
    if args.focus:
        where = "WHERE focus = ?"
        params.append(args.focus.strip().lower())
    sessions = conn.execute(
        f"SELECT focus, date, drills, duration_min FROM practice_sessions {where} "
        "ORDER BY date ASC",
        params,
    ).fetchall()

    if not sessions:
        msg = f"No practice sessions{' for that focus' if args.focus else ''} yet."
        print(msg)
        return 0

    # Group sessions by focus.
    groups: dict[str, list] = {}
    for s in sessions:
        groups.setdefault(s["focus"], []).append(s)

    # Order: SG categories first (in canonical order), then other tags.
    ordered = [c for c in SG_CATEGORIES if c in groups]
    ordered += [f for f in groups if f not in SG_CATEGORIES]

    print("Practice -> results feedback loop\n")
    non_sg = []
    for focus in ordered:
        rows = groups[focus]
        count = len(rows)
        minutes = sum(r["duration_min"] or 0 for r in rows)
        first = rows[0]["date"]
        drills = sorted({r["drills"] for r in rows if r["drills"]})

        if focus not in SG_CATEGORIES:
            non_sg.append((focus, count))
            continue

        # Compare SG/round in this category before vs since first practice.
        lo_before = _shift(first, -args.window) if args.window else None
        hi_after = _shift(first, args.window) if args.window else None
        before = _sg_per_round(conn, focus, date_lo=lo_before, date_hi=first)
        after = _sg_per_round(conn, focus, date_lo=first, date_hi=hi_after)

        head = f"{focus}   ({count} session(s), {minutes} min, since {first})"
        print(head)
        if drills:
            print(f"  drills: {', '.join(drills)}")

        if after is None:
            print("  No rounds logged in this category since you started. "
                  "Go play and log one.\n")
            continue
        if before is None:
            print(f"  SG/round since: {after[0]:+.2f}  ({after[1]} round(s))")
            print("  No before-baseline in this category, so no delta yet.\n")
            continue

        delta = after[0] - before[0]
        arrow = "improved" if delta > 0 else ("worse" if delta < 0 else "flat")
        sign = "^" if delta > 0 else ("v" if delta < 0 else "=")
        print(f"  SG/round before: {before[0]:+.2f} ({before[1]} rd)"
              f"     since: {after[0]:+.2f} ({after[1]} rd)"
              f"     {sign} {arrow} {delta:+.2f}")
        if delta > 0.2:
            print("  -> The work is paying off. Keep going.\n")
        elif delta < -0.2:
            print("  -> Going backwards — change the drill or check your fundamentals.\n")
        else:
            print("  -> Roughly flat. Give it more rounds, or vary the practice.\n")

    if non_sg:
        tags = ", ".join(f"{f} ({n})" for f, n in non_sg)
        print(f"Not tied to an SG category: {tags}")
        print(f"  Tag practice to one of {', '.join(SG_CATEGORIES)} to measure "
              "its strokes-gained impact.")
    return 0
