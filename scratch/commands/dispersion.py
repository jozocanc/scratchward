"""``dispersion`` — real club distances + shot dispersion (Phase 2, built).

Log carry per club (fast: interactive bulk entry or a one-liner), then
report a **reliable** planning distance and the **spread** per club, plus a
gapping view that flags overlaps and holes in your set.

"Reliable" carry is a conservative number you reach most of the time — the
20th-percentile carry once you have a handful of shots (falling back to
mean − 0.85·std, then the mean, for tiny samples). Plan club selection off
that, not your one-in-ten flush. The stats live in :func:`club_stats`
(pure) so they're easy to test.
"""

from __future__ import annotations

import argparse
import math
import statistics
from datetime import date as date_cls, datetime

from .. import db

# Gapping thresholds (yards between adjacent clubs by carry).
GAP_LARGE = 18.0   # bigger than this: a hole worth filling
GAP_OVERLAP = 6.0  # smaller than this: clubs are redundant
RELIABLE_PCTL = 20  # carry you beat ~80% of the time


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("dispersion",
                              help="Club distances and shot dispersion")
    sub = p.add_subparsers(dest="dispersion_command", metavar="<subcommand>")
    sub.required = True

    add = sub.add_parser("add", help="Log a single club shot")
    add.add_argument("--club", required=True, help="Club, e.g. 7i, pw, 3w, driver")
    add.add_argument("--carry", type=float, required=True, help="Carry yards")
    add.add_argument("--side", type=float,
                     help="Lateral offset yards (- left / + right)")
    add.add_argument("--date", default=None, help="Date YYYY-MM-DD (default today)")
    add.set_defaults(func=run_add)

    log = sub.add_parser("log", help="Interactively log many shots, club by club")
    log.add_argument("--date", default=None, help="Date YYYY-MM-DD (default today)")
    log.set_defaults(func=run_log)

    rep = sub.add_parser("report", help="Reliable distance + spread per club")
    rep.add_argument("--days", type=int, default=365,
                     help="Window in days (default 365)")
    rep.set_defaults(func=run_report)


# --------------------------------------------------------------------------- #
# pure stats
# --------------------------------------------------------------------------- #
def _percentile(sorted_vals, p):
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * (p / 100.0)
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return sorted_vals[int(k)]
    return sorted_vals[lo] + (k - lo) * (sorted_vals[hi] - sorted_vals[lo])


def club_stats(carries, sides=None):
    """Distance + dispersion stats for one club's carries. Pure."""
    n = len(carries)
    mean = statistics.fmean(carries)
    std = statistics.stdev(carries) if n > 1 else 0.0
    sc = sorted(carries)
    if n >= 5:
        reliable = _percentile(sc, RELIABLE_PCTL)
    elif n >= 2:
        reliable = mean - 0.85 * std
    else:
        reliable = mean
    out = {"n": n, "mean": mean, "std": std, "reliable": reliable,
           "min": sc[0], "max": sc[-1]}
    valid = [s for s in (sides or []) if s is not None]
    if valid:
        out["side_mean"] = statistics.fmean(valid)
        out["side_std"] = statistics.stdev(valid) if len(valid) > 1 else 0.0
    return out


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _parse_date(value):
    if value is None:
        return date_cls.today().isoformat()
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except ValueError:
        raise SystemExit(f"error: --date must be YYYY-MM-DD, got {value!r}")


def _norm_club(c):
    return c.strip().lower()


def _pretty_club(c):
    return c.upper() if len(c) <= 3 else c.title()


def _side_str(stats):
    if "side_mean" not in stats:
        return "-"
    m = stats["side_mean"]
    d = "R" if m > 0.5 else "L" if m < -0.5 else "·"
    return f"{abs(m):.0f}{d} ±{stats['side_std']:.0f}"


def _insert(conn, date, club, carry, side, notes=None):
    with conn:
        conn.execute(
            "INSERT INTO club_shots (date, club, carry, side, notes) "
            "VALUES (?,?,?,?,?)",
            (date, club, carry, side, notes),
        )


# --------------------------------------------------------------------------- #
# add / log
# --------------------------------------------------------------------------- #
def run_add(args):
    iso = _parse_date(args.date)
    club = _norm_club(args.club)
    conn = db.connect(args.db)
    _insert(conn, iso, club, args.carry, args.side)
    side = "" if args.side is None else f", {abs(args.side):g} yds " \
        + ("right" if args.side > 0 else "left" if args.side < 0 else "center")
    print(f"Logged {_pretty_club(club)}: {args.carry:g} yds carry{side}.")
    return 0


def run_log(args):
    iso = _parse_date(args.date)
    conn = db.connect(args.db)
    print(f"Logging club shots for {iso}. Blank club name to finish.")
    print("Per shot enter carry, optionally a side: '155' or '155, -5' "
          "(- left / + right). Blank carry ends the club.\n")
    total = 0
    try:
        while True:
            club = input("Club (blank = done): ").strip().lower()
            if not club:
                break
            count = 0
            while True:
                raw = input(f"  {_pretty_club(club)} carry: ").strip()
                if not raw:
                    break
                try:
                    parts = [p.strip() for p in raw.split(",")]
                    carry = float(parts[0])
                    side = float(parts[1]) if len(parts) > 1 and parts[1] else None
                except ValueError:
                    print("    ! enter a number, or 'carry, side' — try again")
                    continue
                _insert(conn, iso, club, carry, side)
                count += 1
                total += 1
            if count:
                print(f"  logged {count} {_pretty_club(club)} shot(s)\n")
    except (EOFError, KeyboardInterrupt):
        print()
    print(f"Done — {total} shot(s) saved.")
    return 0


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #
def run_report(args):
    conn = db.connect(args.db)
    rows = conn.execute(
        "SELECT club, carry, side FROM club_shots WHERE date >= date('now', ?)",
        (f"-{int(args.days)} days",),
    ).fetchall()
    if not rows:
        print(f"No club shots logged in the last {args.days} days.")
        print("Log some with:  python -m scratch dispersion log")
        print("            or:  python -m scratch dispersion add --club 7i --carry 155")
        return 0

    by_club: dict[str, list] = {}
    for r in rows:
        by_club.setdefault(r["club"], []).append((r["carry"], r["side"]))

    stats = {c: club_stats([x[0] for x in v], [x[1] for x in v])
             for c, v in by_club.items()}
    # Order long -> short by reliable carry (driver at top, wedges at bottom).
    order = sorted(stats, key=lambda c: stats[c]["reliable"], reverse=True)

    print(f"Club distances & dispersion — last {args.days} days "
          f"({len(rows)} shots)\n")
    header = (f"  {'Club':<8} {'n':>3}  {'Carry':>5}  {'Reliable':>8}  "
              f"{'Spread':>14}  {'Side':>10}")
    print(header)
    print("  " + "-" * (len(header) - 2))
    for c in order:
        s = stats[c]
        spread = f"±{s['std']:.0f} ({s['min']:.0f}-{s['max']:.0f})"
        low = "  *low data" if s["n"] < 3 else ""
        print(f"  {_pretty_club(c):<8} {s['n']:>3}  {s['mean']:>5.0f}  "
              f"{s['reliable']:>8.0f}  {spread:>14}  {_side_str(s):>10}{low}")

    # Gapping.
    print("\nGapping (by reliable carry):")
    for i, c in enumerate(order):
        print(f"  {_pretty_club(c):<8} {stats[c]['reliable']:.0f} yds")
        if i + 1 < len(order):
            gap = stats[c]["reliable"] - stats[order[i + 1]]["reliable"]
            flag = ("   <- large gap, consider filling" if gap > GAP_LARGE
                    else "   <- overlap, clubs are redundant" if gap < GAP_OVERLAP
                    else "")
            print(f"       │ {gap:.0f} yd gap{flag}")

    print(f"\n'Reliable' = the carry you beat ~{100 - RELIABLE_PCTL}% of the time "
          "— plan club selection off this, not your best strike.")
    return 0
