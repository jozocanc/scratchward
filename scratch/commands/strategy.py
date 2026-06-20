"""``strategy`` — on-course strategy from your dispersion (Phase 2, built).

* ``strategy tee``      recommend club + aim off a tee to minimize expected
                        score, given the hole and your real club dispersion.
* ``strategy round``    a full game plan for a saved course — best club/aim
                        per hole, expected score vs par, and the risk holes.
* ``strategy approach`` for a distance to the pin: club, expected proximity,
                        green-in-regulation %, and your typical miss.

All read the per-club distances/dispersion logged via ``dispersion``; the
expected-score math lives in ``scratch.strategy_model`` (pure).
"""

from __future__ import annotations

import argparse

from .. import db
from ..strategy_model import recommend, simulate_approach
from .course import get_course, load_hole
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

    rnd = sub.add_parser("round", help="Full game plan for a saved course")
    rnd.add_argument("--course", required=True, help="Course name from your course book")
    rnd.set_defaults(func=run_round)

    ap = sub.add_parser("approach",
                        help="Club + expected proximity/GIR for a distance")
    ap.add_argument("--distance", type=float, required=True, help="Yards to pin")
    ap.add_argument("--green-radius", type=float, default=9.0,
                    help="Half the green's effective width in yards (default 9)")
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
    print("Log some first:  scratch dispersion log")
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
# strategy round — full game plan from the course book
# --------------------------------------------------------------------------- #
def _aim_abbr(aim) -> str:
    return f"{abs(aim):.0f}{'R' if aim > 0 else 'L'}" if aim else "ctr"


def run_round(args: argparse.Namespace) -> int:
    conn = db.connect(args.db)
    clubs = _load_clubs(conn)
    if not clubs:
        return _no_data()
    course = get_course(conn, args.course)
    if course is None:
        raise SystemExit(f"error: no course named {args.course!r} "
                         "(add holes with `course hole`)")
    holes = conn.execute(
        "SELECT * FROM course_holes WHERE course_id = ? AND length IS NOT NULL "
        "ORDER BY hole", (course["id"],)).fetchall()
    if not holes:
        print(f"{course['name']} has no holes with a length saved yet. Add them "
              "with: course hole --course ... --hole N --par 4 --length 410")
        return 0

    print(f"Game plan — {course['name']}\n")
    header = f"  {'#':>2}  {'Par':>3}  {'Yds':>4}  {'Play':<13} {'Exp':>5}  {'Pen':>4}"
    print(header)
    print("  " + "-" * (len(header) - 2))
    total_exp = 0.0
    total_par = 0
    plays = []
    for h in holes:
        hole = {"length": h["length"],
                "fairway_half": (h["fairway_width"] or 32.0) / 2.0,
                "ob_left": h["ob_left"], "ob_right": h["ob_right"],
                "forced_carry": h["forced_carry"]}
        best = recommend(clubs, hole)[0]
        play = f"{_pretty_club(best['club'])} {_aim_abbr(best['aim'])}"
        par = h["par"] or 0
        total_exp += best["expected"]
        total_par += par
        plays.append((h, best))
        print(f"  {h['hole']:>2}  {par:>3}  {h['length']:>4.0f}  {play:<13} "
              f"{best['expected']:>5.2f}  {best['penalty_rate'] * 100:>3.0f}%")
    print("  " + "-" * (len(header) - 2))
    diff = total_exp - total_par
    print(f"  Expected {total_exp:.1f} vs par {total_par}  ({diff:+.1f} to par)")

    risky = [(h, b) for h, b in plays if b["penalty_rate"] >= 0.03]
    risky.sort(key=lambda p: -p[1]["penalty_rate"])
    if risky:
        print("\n  Risk holes (trouble in play — respect them):")
        for h, b in risky[:3]:
            note = f" — {h['note']}" if h["note"] else ""
            print(f"    #{h['hole']:<2} {_pretty_club(b['club'])} {_aim_abbr(b['aim'])}, "
                  f"penalty {b['penalty_rate'] * 100:.0f}%{note}")
    else:
        print("\n  No high-risk tee shots — play your stock shapes and stay patient.")
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
    gr = args.green_radius
    reaching = [(c, s) for c, s in clubs.items() if s["reliable"] >= d]
    if reaching:
        club, s = min(reaching, key=lambda cs: cs[1]["reliable"])
        pick_note = "reaches without over-swinging"
    else:
        club, s = max(clubs.items(), key=lambda cs: cs[1]["reliable"])
        pick_note = "your longest — expect to come up short"

    sim = simulate_approach(s, d, gr)
    print(f"Approach — {d:.0f} yds to the pin (green ~{gr * 2:.0f} yds wide)\n")
    print(f"Play: {_pretty_club(club)}  (carries {s['mean']:.0f}, reliable "
          f"{s['reliable']:.0f}) — {pick_note}")
    print(f"  Expected proximity {sim['proximity']:.0f} yds   ·   "
          f"green in regulation {sim['gir'] * 100:.0f}%")

    lb, sb = sim["long_bias"], sim["side_bias"]
    long_word = (f"{abs(lb):.0f} yds {'long' if lb > 0 else 'short'}"
                 if abs(lb) >= 1 else "pin-high")
    side_word = (f"{abs(sb):.0f} yds {'right' if sb > 0 else 'left'}"
                 if abs(sb) >= 1 else "center")
    print(f"  Typical miss: {long_word}, {side_word}.")
    if abs(sb) >= 2:
        print(f"  -> You leak {'right' if sb > 0 else 'left'} — favor the "
              f"{'left' if sb > 0 else 'right'} half / fat of the green.")

    ladder = sorted(clubs.items(), key=lambda cs: cs[1]["reliable"], reverse=True)
    print("\n  Your ladder (reliable carry):")
    for c, s2 in ladder:
        mark = "  <-" if c == club else ""
        print(f"    {_pretty_club(c):<8} {s2['reliable']:>4.0f} yds{mark}")
    return 0
