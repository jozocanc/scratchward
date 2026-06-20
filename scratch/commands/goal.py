"""``goal`` — handicap goal tracking (Phase 2, built).

Set a target Handicap Index, then ``goal status`` breaks it into concrete
work: how many strokes/round you need to gain, distributed across your
current strokes-gained leaks (the biggest leak gets the most of the
target), plus a realistic ETA projected from your recent improvement rate.

It reads the existing engines — handicap (`commands.handicap`) and the
shots table — so it needs no new logging. Handicap points are treated as
~1 stroke/round, which is the right order of magnitude for planning.
"""

from __future__ import annotations

import argparse
from datetime import date as date_cls, datetime, timedelta

from .. import db
from ..constants import SG_CATEGORIES
from .handicap import MIN_ROUNDS, compute_handicap_index

DAYS_PER_MONTH = 30.44


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("goal", help="Set and track a handicap goal")
    sub = p.add_subparsers(dest="goal_command", metavar="<subcommand>")
    sub.required = True

    s = sub.add_parser("set", help="Set a target Handicap Index")
    s.add_argument("--handicap", type=float, required=True, help="Target Index")
    s.add_argument("--by", default=None, help="Target date YYYY-MM-DD (optional)")
    s.set_defaults(func=run_set)

    st = sub.add_parser("status", help="Progress + the work to get there")
    st.add_argument("--days", type=int, default=90,
                    help="Window for strokes-gained leaks (default 90)")
    st.set_defaults(func=run_status)

    c = sub.add_parser("clear", help="Clear the active handicap goal")
    c.set_defaults(func=run_clear)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _pretty(text: str) -> str:
    return text.replace("-", " ").title()


def _parse_date(value: str) -> str:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except ValueError:
        raise SystemExit(f"error: --by must be YYYY-MM-DD, got {value!r}")


def _active_goal(conn):
    return conn.execute(
        "SELECT * FROM goals WHERE kind='handicap' AND active=1 "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()


def _rounds(conn):
    return conn.execute(
        "SELECT date, score, course_rating, slope FROM rounds "
        "ORDER BY date DESC, id DESC LIMIT 20"
    ).fetchall()


def _differentials(rows):
    return [(113.0 / r["slope"]) * (r["score"] - r["course_rating"]) for r in rows]


def _sg_by_category(conn, days):
    rows = conn.execute(
        "SELECT category, sg, date, round_id FROM shots "
        "WHERE sg IS NOT NULL AND date >= date('now', ?)",
        (f"-{int(days)} days",),
    ).fetchall()
    agg = {c: [0.0, set()] for c in SG_CATEGORIES}
    for r in rows:
        if r["category"] in agg:
            agg[r["category"]][0] += r["sg"]
            key = ("r", r["round_id"]) if r["round_id"] is not None else ("d", r["date"])
            agg[r["category"]][1].add(key)
    return {c: tot / len(keys) for c, (tot, keys) in agg.items() if keys}


def _improvement_rate(rows):
    """Strokes/month the handicap is improving, from recent rounds.

    Positive == getting better (differentials falling). Returns None if
    there isn't enough dated spread to estimate.
    """
    if len(rows) < 6:
        return None
    diffs = _differentials(rows)
    half = len(rows) // 2
    recent, older = rows[:half], rows[half:half * 2]
    rd, od = diffs[:half], diffs[half:half * 2]

    def mid_ordinal(rs):
        ords = [datetime.strptime(r["date"], "%Y-%m-%d").date().toordinal() for r in rs]
        return sum(ords) / len(ords)

    months = (mid_ordinal(recent) - mid_ordinal(older)) / DAYS_PER_MONTH
    if months <= 0:
        return None
    recent_mean, older_mean = sum(rd) / len(rd), sum(od) / len(od)
    return (older_mean - recent_mean) / months


# --------------------------------------------------------------------------- #
# goal set / clear
# --------------------------------------------------------------------------- #
def run_set(args: argparse.Namespace) -> int:
    target_date = _parse_date(args.by) if args.by else None
    conn = db.connect(args.db)
    with conn:
        conn.execute("UPDATE goals SET active=0 WHERE kind='handicap'")
        conn.execute(
            "INSERT INTO goals (kind, target_value, target_date) VALUES "
            "('handicap', ?, ?)",
            (args.handicap, target_date),
        )
    by = f" by {target_date}" if target_date else ""
    print(f"Goal set: reach a {args.handicap:.1f} Handicap Index{by}.\n")
    return run_status(args)


def run_clear(args: argparse.Namespace) -> int:
    conn = db.connect(args.db)
    with conn:
        n = conn.execute(
            "UPDATE goals SET active=0 WHERE kind='handicap' AND active=1"
        ).rowcount
    print("Active handicap goal cleared." if n else "No active handicap goal.")
    return 0


# --------------------------------------------------------------------------- #
# goal status — the breakdown
# --------------------------------------------------------------------------- #
def run_status(args: argparse.Namespace) -> int:
    conn = db.connect(args.db)
    goal = _active_goal(conn)
    if goal is None:
        print("No handicap goal set. Set one with:")
        print("  python -m scratch goal set --handicap 10 --by 2026-12-31")
        return 0

    rows = _rounds(conn)
    current = compute_handicap_index(_differentials(rows))
    target = goal["target_value"]
    target_date = goal["target_date"]

    by = f" by {target_date}" if target_date else ""
    print(f"Goal: reach a {target:.1f} Handicap Index{by}")

    if current is None:
        print(f"\nNeed at least {MIN_ROUNDS} rounds to measure your Index "
              f"(you have {len(rows)}). Log rounds with `round add`.")
        return 0

    H = current["index"]
    gap = round(H - target, 1)
    print(f"Current Index: {H:.1f}   →   gap: {gap:+.1f} "
          f"({'improve' if gap > 0 else 'already there'})\n")

    if gap <= 0:
        print("You've reached your goal. Set a tougher one with `goal set`, "
              "or clear it with `goal clear`.")
        return 0

    # Where the gain has to come from: distribute the gap across current leaks.
    sg = _sg_by_category(conn, getattr(args, "days", 90))
    leaks = {c: per for c, per in sg.items() if per < 0}
    total_leak = sum(abs(v) for v in leaks.values())
    print(f"You need to gain ~{gap:.1f} strokes/round. Where it comes from:")
    if total_leak > 0:
        for cat, per in sorted(leaks.items(), key=lambda kv: kv[1]):
            improve = gap * (abs(per) / total_leak)
            print(f"  • {_pretty(cat):<13} improve {improve:+.1f}/round  "
                  f"(now {per:+.1f} → {per + improve:+.1f})")
        if gap > total_leak + 0.05:
            print(f"  Note: erasing all current leaks is ~{total_leak:.1f} "
                  f"strokes — the last {gap - total_leak:.1f} must come from "
                  f"raising your stronger areas above average.")
    else:
        print("  No clear strokes-gained leaks logged. Log shots with `sg` to "
              "get category-by-category targets; for now the gain must come "
              "from all-round consistency.")

    # Timeline.
    rate = _improvement_rate(rows)
    print()
    if target_date:
        days_left = (datetime.strptime(target_date, "%Y-%m-%d").date()
                     - date_cls.today()).days
        months_left = days_left / DAYS_PER_MONTH
        if months_left <= 0:
            print(f"Target date {target_date} has passed — reset it with `goal set`.")
        else:
            need_rate = gap / months_left
            print(f"Timeline: {days_left} days left → you'd need to improve "
                  f"~{need_rate:.1f} strokes/month.")
            if rate is not None:
                verdict = ("on pace" if rate >= need_rate else "behind pace")
                print(f"  Recent rate: {rate:+.1f} strokes/month — {verdict}.")
            else:
                print("  Log more dated rounds to judge whether you're on pace.")
    else:
        if rate and rate > 0:
            eta_months = gap / rate
            eta = date_cls.today() + timedelta(days=eta_months * DAYS_PER_MONTH)
            m = max(1, round(eta_months))
            unit = "month" if m == 1 else "months"
            print(f"At your recent rate ({rate:+.1f} strokes/month), ETA "
                  f"~{m} {unit} → around {eta.isoformat()}.")
        elif rate is not None:
            print("At your recent rate you're not trending toward this goal — "
                  "the practice plan below is how you change that.")
        else:
            print("Log more dated rounds for an ETA estimate.")

    print("\nYour trainer already targets these leaks: python -m scratch train")
    return 0
