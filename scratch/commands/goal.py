"""``goal`` — handicap goal tracking (Phase 2, built; deepened).

Set a target Handicap Index and track the whole journey:

* ``goal set``     captures a baseline (today's Index) and the target.
* ``goal status``  progress bar + % closed, a trajectory sparkline of your
                   Index over time, a milestone ladder, the per-category
                   strokes you still need (with how each is moving since you
                   set the goal), and a realistic pace/ETA.
* ``goal project`` a what-if: "if I gain +1.0 on approach and +0.5 putting,
                   where does my Index land?"
* ``goal clear``   drop the active goal.

It reads the existing engines — handicap and the shots table — so it needs
no new logging. A handicap point is treated as ~1 stroke/round, the right
order of magnitude for planning.
"""

from __future__ import annotations

import argparse
import math
import sys
from datetime import date as date_cls, datetime, timedelta

from .. import db
from ..constants import SG_CATEGORIES
from .handicap import MIN_ROUNDS, compute_handicap_index

DAYS_PER_MONTH = 30.44
_SPARK = "▁▂▃▄▅▆▇█"


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("goal", help="Set and track a handicap goal")
    sub = p.add_subparsers(dest="goal_command", metavar="<subcommand>")
    sub.required = True

    s = sub.add_parser("set", help="Set a target Handicap Index")
    s.add_argument("--handicap", type=float, required=True, help="Target Index")
    s.add_argument("--by", default=None, help="Target date YYYY-MM-DD (optional)")
    s.set_defaults(func=run_set)

    st = sub.add_parser("status", help="Progress, milestones, and the work to get there")
    st.add_argument("--days", type=int, default=90,
                    help="Window for strokes-gained leaks (default 90)")
    st.set_defaults(func=run_status)

    pr = sub.add_parser("project", help="What-if: project your Index from SG gains")
    pr.add_argument("--off-the-tee", type=float, default=0.0, help="Strokes/round gained")
    pr.add_argument("--approach", type=float, default=0.0)
    pr.add_argument("--short-game", type=float, default=0.0)
    pr.add_argument("--putting", type=float, default=0.0)
    pr.set_defaults(func=run_project)

    c = sub.add_parser("clear", help="Clear the active handicap goal")
    c.set_defaults(func=run_clear)


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _color() -> bool:
    return sys.stdout.isatty()


def _c(s, code) -> str:
    return f"\033[{code}m{s}\033[0m" if _color() else s


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


def _sg_window(conn, date_lo=None, date_hi=None):
    """Strokes gained per round per category, optionally bounded by date."""
    clauses = ["sg IS NOT NULL"]
    params: list = []
    if date_lo:
        clauses.append("date >= ?")
        params.append(date_lo)
    if date_hi:
        clauses.append("date < ?")
        params.append(date_hi)
    rows = conn.execute(
        "SELECT category, sg, date, round_id FROM shots WHERE " + " AND ".join(clauses),
        params,
    ).fetchall()
    agg = {c: [0.0, set()] for c in SG_CATEGORIES}
    for r in rows:
        if r["category"] in agg:
            agg[r["category"]][0] += r["sg"]
            key = ("r", r["round_id"]) if r["round_id"] is not None else ("d", r["date"])
            agg[r["category"]][1].add(key)
    return {c: tot / len(keys) for c, (tot, keys) in agg.items() if keys}


def _improvement_rate(rows):
    """Strokes/month the handicap is improving (positive == getting better)."""
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
    return (sum(od) / len(od) - sum(rd) / len(rd)) / months


def _index_history(conn, points=14):
    """Rolling Handicap Index at each of the last `points` rounds."""
    rows = conn.execute(
        "SELECT date, score, course_rating, slope FROM rounds ORDER BY date ASC, id ASC"
    ).fetchall()
    if len(rows) < MIN_ROUNDS:
        return []
    diffs = [((113.0 / r["slope"]) * (r["score"] - r["course_rating"]), r["date"])
             for r in rows]
    out = []
    for j in range(max(MIN_ROUNDS, len(diffs) - points), len(diffs) + 1):
        res = compute_handicap_index([d for d, _ in diffs[max(0, j - 20):j]])
        if res:
            out.append((diffs[j - 1][1], res["index"]))
    return out


def _sparkline(vals):
    if not vals:
        return ""
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-9:
        return _SPARK[3] * len(vals)
    return "".join(_SPARK[int((v - lo) / (hi - lo) * (len(_SPARK) - 1))] for v in vals)


def _bar(frac, width=22):
    frac = max(0.0, min(1.0, frac))
    filled = int(round(frac * width))
    return "█" * filled + "░" * (width - filled)


def _milestones(start, target):
    ms = []
    m = math.floor(start - 1e-6)
    while m > target + 1e-9:
        ms.append(float(m))
        m -= 1
    ms.append(round(float(target), 1))
    return ms


def category_targets(conn, days=90):
    """Per-category strokes the active goal needs, for the trainer to aim at.

    Returns (target_value, target_date, gap, {cat: improve_per_round}) for the
    active handicap goal, or None if there's no goal or no measurable Index.
    The dict is empty when the goal is already reached or no leaks are logged.
    """
    goal = _active_goal(conn)
    if goal is None:
        return None
    current = compute_handicap_index(_differentials(_rounds(conn)))
    if current is None:
        return None
    gap = round(current["index"] - goal["target_value"], 1)
    targets: dict = {}
    if gap > 0:
        leaks = {c: per for c, per in _recent_sg(conn, days).items() if per < 0}
        total = sum(abs(v) for v in leaks.values())
        if total > 0:
            targets = {c: gap * (abs(per) / total) for c, per in leaks.items()}
    return (goal["target_value"], goal["target_date"], gap, targets)


# --------------------------------------------------------------------------- #
# goal set / clear
# --------------------------------------------------------------------------- #
def run_set(args: argparse.Namespace) -> int:
    target_date = _parse_date(args.by) if args.by else None
    conn = db.connect(args.db)
    current = compute_handicap_index(_differentials(_rounds(conn)))
    start_value = current["index"] if current else None
    start_date = date_cls.today().isoformat()
    with conn:
        conn.execute("UPDATE goals SET active=0 WHERE kind='handicap'")
        conn.execute(
            "INSERT INTO goals (kind, target_value, target_date, start_value, "
            "start_date) VALUES ('handicap', ?, ?, ?, ?)",
            (args.handicap, target_date, start_value, start_date),
        )
    by = f" by {target_date}" if target_date else ""
    print(f"Goal set: reach a {args.handicap:.1f} Handicap Index{by}.")
    if start_value is not None:
        print(f"Baseline captured: {start_value:.1f} as of {start_date}.\n")
    else:
        print()
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
# goal status
# --------------------------------------------------------------------------- #
def run_status(args: argparse.Namespace) -> int:
    conn = db.connect(args.db)
    goal = _active_goal(conn)
    if goal is None:
        print("No handicap goal set. Set one with:")
        print("  scratch goal set --handicap 10 --by 2026-12-31")
        return 0

    rows = _rounds(conn)
    current = compute_handicap_index(_differentials(rows))
    target = goal["target_value"]
    target_date = goal["target_date"]
    by = f" by {target_date}" if target_date else ""
    print(_c(f"Goal: reach a {target:.1f} Handicap Index{by}", "1;36"))

    if current is None:
        print(f"\nNeed at least {MIN_ROUNDS} rounds to measure your Index "
              f"(you have {len(rows)}). Log rounds with `round add`.")
        return 0

    H = current["index"]
    keys = goal.keys()
    start_value = goal["start_value"] if "start_value" in keys and goal["start_value"] \
        is not None else H
    start_date = goal["start_date"] if "start_date" in keys else None

    # Progress bar (baseline -> current -> target).
    total_gap = start_value - target
    if total_gap > 1e-9:
        closed = start_value - H
        frac = closed / total_gap
        print(f"\n  {start_value:>4.1f}  {_c(_bar(frac), '32')}  {target:.1f}")
        print(_c(f"        closed {closed:+.1f} of {total_gap:.1f} strokes "
                 f"({max(0, min(100, round(frac * 100)))}%) — now {H:.1f}", "2"))
    else:
        print(f"\n  Current {H:.1f}   Target {target:.1f}")

    gap = round(H - target, 1)
    if gap <= 0:
        print(_c("\n  * Goal reached! Set a tougher one with `goal set`, "
                 "or `goal clear`.", "1;32"))
        return 0

    # Trajectory sparkline.
    hist = _index_history(conn)
    if len(hist) >= 3:
        vals = [v for _, v in hist]
        print(f"\n  Trajectory  {_c(_sparkline(vals), '36')}  "
              + _c(f"({len(vals)} rounds: {vals[0]:.1f} -> {vals[-1]:.1f}, "
                   f"{vals[-1] - vals[0]:+.1f})", "2"))

    # Milestone ladder.
    ms = _milestones(start_value, target)
    if len(ms) > 1:
        print("\n  Milestones")
        marked_next = False
        for m in ms:
            goal_tag = "  goal" if abs(m - target) < 1e-9 else ""
            if H <= m + 1e-9:
                print(f"    {_c('[x]', '32')} {m:>4.1f}   {_c('reached', '32')}{goal_tag}")
            elif not marked_next:
                marked_next = True
                print(f"    {_c('[>]', '1;33')} {m:>4.1f}   "
                      f"{_c(f'next, {H - m:.1f} to go', '33')}{goal_tag}")
            else:
                print(f"    [ ] {m:>4.1f}{goal_tag}")

    # Per-category work, with movement since the goal was set.
    sg = _recent_sg(conn, getattr(args, "days", 90))
    leaks = {c: per for c, per in sg.items() if per < 0}
    total_leak = sum(abs(v) for v in leaks.values())
    print(f"\n  To reach {target:.1f} you need ~{gap:.1f} more strokes/round:")
    if total_leak > 0:
        before = _sg_window(conn, date_hi=start_date) if start_date else {}
        since = _sg_window(conn, date_lo=start_date) if start_date else {}
        for cat, per in sorted(leaks.items(), key=lambda kv: kv[1]):
            improve = gap * (abs(per) / total_leak)
            line = (f"    {_pretty(cat):<12} target {improve:+.1f}/round   "
                    f"(now {per:+.1f}")
            if cat in before and cat in since:
                d = since[cat] - before[cat]
                arrow = (_c("up", "32") if d > 0.05 else _c("down", "31")
                         if d < -0.05 else "flat")
                line += f", {arrow} {d:+.1f} since goal"
            print(line + ")")
        if gap > total_leak + 0.05:
            print(_c(f"    (clearing every leak is ~{total_leak:.1f} strokes; the "
                     f"last {gap - total_leak:.1f} comes from raising your "
                     f"stronger areas)", "2"))
    else:
        print("    No strokes-gained leaks logged — log shots with `sg` for "
              "category targets.")

    _print_pace(rows, gap, target_date)
    print(_c("\n  Plan: scratch train    What-if: scratch goal project "
             "--approach 1.0", "2"))
    return 0


def _recent_sg(conn, days):
    return _sg_window(conn, date_lo=db_date_days_ago(conn, days))


def db_date_days_ago(conn, days):
    return conn.execute("SELECT date('now', ?)", (f"-{int(days)} days",)).fetchone()[0]


def _print_pace(rows, gap, target_date) -> None:
    rate = _improvement_rate(rows)
    print()
    if target_date:
        days_left = (datetime.strptime(target_date, "%Y-%m-%d").date()
                     - date_cls.today()).days
        months_left = days_left / DAYS_PER_MONTH
        if months_left <= 0:
            print(f"  Target date {target_date} has passed — reset it with `goal set`.")
            return
        need = gap / months_left
        print(f"  Pace: {days_left} days left, need ~{need:.1f} strokes/month.")
        if rate is not None:
            verdict = _c("on pace", "32") if rate >= need else _c("behind pace", "33")
            print(f"        recent rate {rate:+.1f}/month — {verdict}.")
        else:
            print("        log more dated rounds to judge pace.")
    else:
        if rate and rate > 0:
            m = max(1, round(gap / rate))
            eta = date_cls.today() + timedelta(days=(gap / rate) * DAYS_PER_MONTH)
            print(f"  Pace: at {rate:+.1f}/month, ETA ~{m} "
                  f"month{'' if m == 1 else 's'} (around {eta.isoformat()}).")
        elif rate is not None:
            print("  Pace: not trending toward this goal yet — the plan below "
                  "is how you change that.")
        else:
            print("  Pace: log more dated rounds for an ETA.")


# --------------------------------------------------------------------------- #
# goal project — what-if
# --------------------------------------------------------------------------- #
def run_project(args: argparse.Namespace) -> int:
    conn = db.connect(args.db)
    current = compute_handicap_index(_differentials(_rounds(conn)))
    if current is None:
        print(f"Need at least {MIN_ROUNDS} rounds to project an Index.")
        return 0
    H = current["index"]
    imp = {
        "off-the-tee": args.off_the_tee, "approach": args.approach,
        "short-game": args.short_game, "putting": args.putting,
    }
    total = sum(imp.values())
    if total == 0:
        print("Pass one or more category gains, e.g.:")
        print("  scratch goal project --approach 1.0 --putting 0.5")
        return 0
    new = round(H - total, 1)
    print(_c("Projection (a handicap point ~ 1 stroke/round)\n", "1;36"))
    print(f"  Current Index: {H:.1f}")
    for c, v in imp.items():
        if v:
            print(f"    {_pretty(c):<12} {v:+.1f}/round")
    print(f"  -> Projected Index: ~{new:.1f}   ({-total:+.1f} strokes/round)")
    goal = _active_goal(conn)
    if goal is not None:
        t = goal["target_value"]
        if new <= t:
            print(_c(f"  That reaches your {t:.1f} goal.", "1;32"))
        else:
            print(_c(f"  Still {new - t:.1f} short of your {t:.1f} goal.", "33"))
    return 0
