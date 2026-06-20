"""``strategy`` — on-course strategy from your dispersion (Phase 2, built).

* ``strategy tee``      recommend club + aim off a tee to minimize expected
                        score, given the hole and your real club dispersion.
* ``strategy approach`` quick club pick for a given distance to the pin.

Both read the per-club distances/dispersion logged via ``dispersion``; the
expected-score math lives in ``scratch.strategy_model`` (pure).
"""

from __future__ import annotations

import argparse

from .. import db
from ..strategy_model import recommend
from .course import load_hole
from .dispersion import club_stats, _pretty_club


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("strategy",
                              help="On-course club/target strategy from dispersion")
    sub = p.add_subparsers(dest="strategy_command", metavar="<subcommand>")
    sub.required = True

    tee = sub.add_parser("tee", help="Best club + aim off the tee")
    tee.add_argument("--length", type=float, default=None,
                     help="Yards from tee to the pin (or load from a saved hole)")
    tee.add_argument("--course", help="Load hole geometry from your course book")
    tee.add_argument("--hole", type=int, help="Hole number in --course")
    tee.add_argument("--par", type=int, default=None, choices=(3, 4, 5))
    tee.add_argument("--fairway-width", type=float, default=None,
                     help="Total fairway width in yards (default 32)")
    tee.add_argument("--ob-left", type=float, default=None,
                     help="Penalty/OB this many yards left of center")
    tee.add_argument("--ob-right", type=float, default=None,
                     help="Penalty/OB this many yards right of center")
    tee.add_argument("--forced-carry", type=float, default=None,
                     help="Must-carry distance (water/waste short of landing)")
    tee.set_defaults(func=run_tee)

    ap = sub.add_parser("approach", help="Club pick for a distance to the pin")
    ap.add_argument("--distance", type=float, required=True, help="Yards to pin")
    ap.set_defaults(func=run_approach)


def _load_clubs(conn, days=365) -> dict:
    rows = conn.execute(
        "SELECT club, carry, side FROM club_shots WHERE date >= date('now', ?)",
        (f"-{int(days)} days",),
    ).fetchall()
    by_club: dict[str, list] = {}
    for r in rows:
        by_club.setdefault(r["club"], []).append((r["carry"], r["side"]))
    return {c: club_stats([x[0] for x in v], [x[1] for x in v])
            for c, v in by_club.items()}


def _aim_str(aim: float) -> str:
    if aim == 0:
        return "center"
    return f"{abs(aim):.0f} yds {'right' if aim > 0 else 'left'}"


def _no_data() -> int:
    print("No club dispersion logged yet — strategy needs your real distances.")
    print("Log some first:  python -m scratch dispersion log")
    return 1


# --------------------------------------------------------------------------- #
# strategy tee
# --------------------------------------------------------------------------- #
def run_tee(args: argparse.Namespace) -> int:
    conn = db.connect(args.db)
    clubs = _load_clubs(conn)
    if not clubs:
        return _no_data()

    # Optionally load a saved hole; explicit flags override saved values.
    saved = None
    note = None
    if args.course or args.hole is not None:
        if not (args.course and args.hole is not None):
            raise SystemExit("error: use --course and --hole together")
        saved = load_hole(conn, args.course, args.hole)
        if saved is None:
            raise SystemExit(
                f"error: no saved hole {args.hole} for {args.course!r} "
                "(add it with `course hole`)")
        note = saved["note"]

    def pick(flag, key, default):
        if flag is not None:
            return flag
        if saved is not None and saved[key] is not None:
            return saved[key]
        return default

    length = pick(args.length, "length", None)
    if length is None:
        raise SystemExit("error: provide --length, or --course/--hole for a "
                         "saved hole that has a length")
    par = pick(args.par, "par", 4)
    fairway_width = pick(args.fairway_width, "fairway_width", 32.0)
    ob_left = pick(args.ob_left, "ob_left", None)
    ob_right = pick(args.ob_right, "ob_right", None)
    forced_carry = pick(args.forced_carry, "forced_carry", None)

    hole = {
        "length": length,
        "fairway_half": fairway_width / 2.0,
        "ob_left": ob_left,
        "ob_right": ob_right,
        "forced_carry": forced_carry,
    }
    results = recommend(clubs, hole)

    where = f"  ({args.course} #{args.hole})" if saved else ""
    print(f"Tee strategy — par {par}, {length:.0f} yds{where}")
    conditions = [f"fairway {fairway_width:.0f} yds wide"]
    if ob_left is not None:
        conditions.append(f"OB/penalty {ob_left:.0f} yds left")
    if ob_right is not None:
        conditions.append(f"OB/penalty {ob_right:.0f} yds right")
    if forced_carry is not None:
        conditions.append(f"forced carry {forced_carry:.0f} yds")
    print("  " + "; ".join(conditions))
    if note:
        print(f"  note: {note}")
    print()

    header = (f"  {'#':>2}  {'Club':<8} {'Aim':>8}  {'Exp':>5}  "
              f"{'Fairway':>7}  {'Penalty':>7}  {'Leave':>5}")
    print(header)
    print("  " + "-" * (len(header) - 2))
    for i, r in enumerate(results[:5], 1):
        aim = (f"{abs(r['aim']):.0f}{'R' if r['aim'] > 0 else 'L'}"
               if r["aim"] else "ctr")
        print(f"  {i:>2}  {_pretty_club(r['club']):<8} {aim:>8}  "
              f"{r['expected']:>5.2f}  {r['fairway_rate']*100:>6.0f}%  "
              f"{r['penalty_rate']*100:>6.0f}%  {r['avg_leave']:>5.0f}")

    best = results[0]
    print(f"\nRecommended: {_pretty_club(best['club'])}, aim {_aim_str(best['aim'])} "
          f"(expected {best['expected']:.2f}, {best['avg_leave']:.0f} yd leave).")

    # Compare to the club that leaves the shortest approach (the aggressive
    # play) if it isn't the pick — name the tradeoff.
    longest = min(results, key=lambda r: r["avg_leave"])
    if longest["club"] != best["club"]:
        delta = longest["expected"] - best["expected"]
        print(f"  {_pretty_club(longest['club'])} leaves less in "
              f"({longest['avg_leave']:.0f} yds) but costs {delta:+.2f} expected "
              f"and finds trouble {longest['penalty_rate']*100:.0f}% of the time.")
    return 0


# --------------------------------------------------------------------------- #
# strategy approach
# --------------------------------------------------------------------------- #
def run_approach(args: argparse.Namespace) -> int:
    conn = db.connect(args.db)
    clubs = _load_clubs(conn)
    if not clubs:
        return _no_data()

    d = args.distance
    # Prefer the shortest club whose reliable carry still reaches the pin
    # (carry the front), else the longest club available.
    reaching = [(c, s) for c, s in clubs.items() if s["reliable"] >= d]
    print(f"Approach — {d:.0f} yds to the pin\n")
    if reaching:
        club, s = min(reaching, key=lambda cs: cs[1]["reliable"])
        print(f"Play: {_pretty_club(club)}  "
              f"(reliable {s['reliable']:.0f}, avg {s['mean']:.0f} yds)")
        print(f"  Reaches {d:.0f} most of the time without over-swinging.")
    else:
        club, s = max(clubs.items(), key=lambda cs: cs[1]["reliable"])
        print(f"Play: {_pretty_club(club)}  (your longest — reliable {s['reliable']:.0f} yds)")
        print(f"  {d:.0f} is past your reliable carry; expect to come up short — "
              f"aim for the front and take your medicine.")
    # Show the neighbors for context.
    ladder = sorted(clubs.items(), key=lambda cs: cs[1]["reliable"], reverse=True)
    print("\n  Your ladder (reliable carry):")
    for c, s in ladder:
        mark = "  <-" if c == club else ""
        print(f"    {_pretty_club(c):<8} {s['reliable']:>4.0f} yds{mark}")
    return 0
