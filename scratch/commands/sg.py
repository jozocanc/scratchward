"""``sg`` — strokes gained. Fully implemented.

Three subcommands:

* ``sg log``    interactive, hole-by-hole; the end of each shot auto-fills
                the start of the next, so logging a round is fast.
* ``sg add``    one-liner for a single shot, scriptable.
* ``sg report`` strokes gained per category over a window, with the
                biggest leak called out.

The strokes-gained math lives in ``scratch.strokes_gained`` (pure); this
module is the CLI + persistence around it.
"""

from __future__ import annotations

import argparse
from datetime import date as date_cls, datetime

from .. import db
from ..constants import LIES, SG_CATEGORIES
from ..strokes_gained import classify, strokes_gained

# Single-letter shortcuts for fast interactive entry.
LIE_SHORTCUTS = {
    "t": "tee",
    "f": "fairway",
    "r": "rough",
    "s": "sand",
    "c": "recovery",
    "g": "green",
}


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("sg", help="Strokes gained: log shots and report leaks")
    sub = p.add_subparsers(dest="sg_command", metavar="<subcommand>")
    sub.required = True

    log = sub.add_parser("log", help="Interactively log a round's shots")
    log.add_argument("--date", default=None, help="Round date YYYY-MM-DD (default today)")
    log.add_argument("--round-id", type=int, default=None, help="Link shots to a round")
    log.add_argument("--start-hole", type=int, default=1, help="First hole number")
    log.set_defaults(func=run_log)

    add = sub.add_parser("add", help="Log a single shot via flags")
    add.add_argument("--start", type=float, required=True, help="Start distance to pin")
    add.add_argument("--lie", required=True, choices=LIES, help="Start lie")
    add.add_argument("--end", type=float, help="End distance to pin (omit if holed)")
    add.add_argument(
        "--end-lie", choices=LIES, default="green", help="End lie (default green)"
    )
    add.add_argument("--holed", action="store_true", help="Shot was holed out")
    add.add_argument("--penalty", type=int, default=0, help="Penalty strokes")
    add.add_argument("--par", type=int, choices=(3, 4, 5), help="Hole par (tee shots)")
    add.add_argument("--hole", type=int, help="Hole number")
    add.add_argument("--date", default=None, help="Date YYYY-MM-DD (default today)")
    add.add_argument("--round-id", type=int, default=None, help="Link to a round")
    add.add_argument(
        "--category", choices=SG_CATEGORIES, help="Override auto-attribution"
    )
    add.set_defaults(func=run_add)

    rep = sub.add_parser("report", help="Strokes gained per category over a window")
    rep.add_argument("--days", type=int, default=90, help="Window in days (default 90)")
    rep.set_defaults(func=run_report)


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


def _unit(lie: str) -> str:
    return "ft" if lie == "green" else "yds"


def _insert_shot(conn, *, date, round_id, hole, shot_num, start_distance, start_lie,
                 end_distance, end_lie, holed, penalty, category, sg) -> int:
    with conn:
        cur = conn.execute(
            "INSERT INTO shots (round_id, date, hole, shot_num, start_distance, "
            "start_lie, end_distance, end_lie, holed, penalty, category, sg) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (round_id, date, hole, shot_num, start_distance, start_lie,
             end_distance, end_lie, 1 if holed else 0, penalty, category, sg),
        )
    return cur.lastrowid


# --------------------------------------------------------------------------- #
# sg add — one-liner
# --------------------------------------------------------------------------- #
def run_add(args: argparse.Namespace) -> int:
    if not args.holed and args.end is None:
        raise SystemExit("error: provide --end <distance> or --holed")
    iso_date = _parse_date(args.date)
    category = args.category or classify(args.lie, args.start, args.par)
    sg = strokes_gained(
        args.start,
        args.lie,
        end_distance=args.end,
        end_lie=args.end_lie,
        holed=args.holed,
        penalty=args.penalty,
    )
    conn = db.connect(args.db)
    shot_id = _insert_shot(
        conn,
        date=iso_date,
        round_id=args.round_id,
        hole=args.hole,
        shot_num=None,
        start_distance=args.start,
        start_lie=args.lie,
        end_distance=None if args.holed else args.end,
        end_lie=None if args.holed else args.end_lie,
        holed=args.holed,
        penalty=args.penalty,
        category=category,
        sg=sg,
    )
    where = "holed out" if args.holed else f"to {args.end:g} {_unit(args.end_lie)} ({args.end_lie})"
    print(
        f"Shot #{shot_id} logged: {args.start:g} {_unit(args.lie)} ({args.lie}) {where}"
        + (f", {args.penalty} penalty" if args.penalty else "")
    )
    print(f"  category: {category}   strokes gained: {sg:+.2f}")
    return 0


# --------------------------------------------------------------------------- #
# sg log — interactive, hole-by-hole
# --------------------------------------------------------------------------- #
def _prompt(text: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default is not None else ""
    try:
        raw = input(f"{text}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raise _Abort()
    if not raw and default is not None:
        return default
    return raw


class _Abort(Exception):
    pass


def _parse_lie(token: str) -> str:
    token = token.lower()
    if token in LIE_SHORTCUTS:
        return LIE_SHORTCUTS[token]
    if token in LIES:
        return token
    raise ValueError(f"unknown lie {token!r} (use t/f/r/s/c/g or full name)")


def _parse_result(raw: str):
    """Parse a compact shot-result token.

    Forms:
        'h' or 'holed'        -> holed out
        '<lie> <distance>'    -> ended at a lie + distance
    Optional trailing '+N'    -> N penalty strokes (e.g. 'r 90 +1')

    Returns (holed, end_lie, end_distance, penalty).
    """
    parts = raw.split()
    penalty = 0
    if parts and parts[-1].startswith("+"):
        penalty = int(parts[-1][1:])
        parts = parts[:-1]
    if not parts:
        raise ValueError("empty result")
    if parts[0].lower() in ("h", "holed"):
        return True, None, None, penalty
    if len(parts) < 2:
        raise ValueError("need '<lie> <distance>' or 'h' for holed")
    end_lie = _parse_lie(parts[0])
    end_distance = float(parts[1])
    return False, end_lie, end_distance, penalty


def run_log(args: argparse.Namespace) -> int:
    iso_date = _parse_date(args.date)
    conn = db.connect(args.db)
    print(f"Logging shots for {iso_date}"
          + (f" (round #{args.round_id})" if args.round_id else "")
          + ". Enter a blank hole number when done.\n")
    print("Result shorthand:  'f 140' (fairway, 140y) · 'g 25' (green, 25ft) · "
          "'h' (holed) · add '+1' for a penalty\n")

    totals = {cat: 0.0 for cat in SG_CATEGORIES}
    shot_count = 0
    hole = args.start_hole
    try:
        while True:
            raw_hole = _prompt(f"Hole #{hole} (blank = finish)", default="")
            if raw_hole == "":
                break
            try:
                hole = int(raw_hole)
            except ValueError:
                print("  (enter a hole number, or blank to finish)")
                continue
            par = int(_prompt("  Par", default="4"))
            start_distance = float(_prompt("  Tee shot — distance to pin (yds)"))
            start_lie = "tee"
            shot_num = 1
            hole_sg = 0.0
            while True:
                result_raw = _prompt(f"    shot {shot_num} from {start_distance:g}"
                                     f" {_unit(start_lie)} ({start_lie}) → result")
                try:
                    holed, end_lie, end_distance, penalty = _parse_result(result_raw)
                    sg = strokes_gained(
                        start_distance, start_lie,
                        end_distance=end_distance, end_lie=end_lie,
                        holed=holed, penalty=penalty,
                    )
                except ValueError as exc:
                    print(f"      ! {exc} — try again")
                    continue
                category = classify(start_lie, start_distance, par)
                _insert_shot(
                    conn, date=iso_date, round_id=args.round_id, hole=hole,
                    shot_num=shot_num, start_distance=start_distance,
                    start_lie=start_lie, end_distance=end_distance, end_lie=end_lie,
                    holed=holed, penalty=penalty, category=category, sg=sg,
                )
                totals[category] += sg
                hole_sg += sg
                shot_count += 1
                print(f"      {category}: {sg:+.2f}")
                if holed:
                    break
                start_distance, start_lie = end_distance, end_lie
                shot_num += 1
            print(f"  Hole {hole}: {shot_num} shots, {hole_sg:+.2f} SG\n")
            hole += 1
    except _Abort:
        print("\nStopped. Shots logged so far are saved.")

    if shot_count:
        print("Session totals (strokes gained):")
        for cat in SG_CATEGORIES:
            print(f"  {cat:<13} {totals[cat]:+.2f}")
        print(f"  {'TOTAL':<13} {sum(totals.values()):+.2f}  ({shot_count} shots)")
    else:
        print("No shots logged.")
    return 0


# --------------------------------------------------------------------------- #
# sg report
# --------------------------------------------------------------------------- #
def run_report(args: argparse.Namespace) -> int:
    conn = db.connect(args.db)
    cutoff = f"-{int(args.days)} days"
    rows = conn.execute(
        "SELECT date, round_id, category, sg, start_lie, start_distance "
        "FROM shots WHERE sg IS NOT NULL AND date >= date('now', ?)",
        (cutoff,),
    ).fetchall()

    if not rows:
        print(f"No shots logged in the last {args.days} days.")
        print("Log some with: python -m scratch sg log   (or  sg add ...)")
        return 0

    # A "round" = a distinct round_id, or a distinct date when unlinked.
    round_keys = set()
    by_cat = {cat: 0.0 for cat in SG_CATEGORIES}
    inside_100 = 0.0
    for r in rows:
        round_keys.add(("r", r["round_id"]) if r["round_id"] is not None
                       else ("d", r["date"]))
        if r["category"] in by_cat:
            by_cat[r["category"]] += r["sg"]
        if r["start_lie"] != "green" and (r["start_distance"] or 0) <= 100:
            inside_100 += r["sg"]
    n_rounds = max(len(round_keys), 1)

    worst = min(by_cat, key=lambda c: by_cat[c] / n_rounds)
    print(f"Strokes gained — last {args.days} days "
          f"({len(round_keys)} round(s), {len(rows)} shots)\n")
    for cat in SG_CATEGORIES:
        per = by_cat[cat] / n_rounds
        flag = "   <- biggest leak" if cat == worst and by_cat[cat] < 0 else ""
        print(f"  {cat:<13} {per:+5.2f} / round   ({by_cat[cat]:+6.1f} total){flag}")
    total_per = sum(by_cat.values()) / n_rounds
    print(f"  {'-'*11}")
    print(f"  {'TOTAL':<13} {total_per:+5.2f} / round")
    print(f"\n  Inside 100 yds: {inside_100 / n_rounds:+.2f} / round "
          f"(approach + short game <=100y)")

    if by_cat[worst] < 0:
        shots_lost = abs(by_cat[worst] / n_rounds)
        print(f"\nYou're losing ~{shots_lost:.1f} shots/round on {worst}. "
              f"Practice that first.")
    else:
        print("\nNo category is bleeding strokes right now — nice.")
    return 0
