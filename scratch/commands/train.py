"""``train`` — personalized practice + mobility routine. Fully implemented.

This is the capstone that ties the tool together. It reads your real data
in the priority order from the spec:

    1. strokes-gained leaks   (shots table — worst category first)
    2. swing-analysis faults  (swing_analyses table — most recent)
    3. handicap trend         (rounds table — direction + level)

then selects from the built-in drill/mobility library
(``scratch.data.drills``), each entry tagged with the SG category and/or
swing-fault it addresses, and prints what to practice and why, in priority
order, inside a time budget (``--minutes``).
"""

from __future__ import annotations

import argparse
import json

from .. import db
from ..constants import SG_CATEGORIES
from ..data.drills import DRILLS, MOBILITY
from .handicap import compute_handicap_index

# A category is a "leak" once it's costing more than this per round.
LEAK_THRESHOLD = -0.1
MAX_DRILLS_PER_NEED = 2


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("train", help="Build a personalized practice routine")
    p.add_argument("--minutes", type=int, default=60, help="Time budget (default 60)")
    p.add_argument("--days", type=int, default=90,
                   help="Window for strokes-gained leaks (default 90)")
    p.set_defaults(func=run)


# --------------------------------------------------------------------------- #
# data readers
# --------------------------------------------------------------------------- #
def _sg_by_category(conn, days: int) -> dict:
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
    return {c: (tot / len(keys), len(keys)) for c, (tot, keys) in agg.items() if keys}


def _recent_faults(conn) -> list[tuple[str, int]]:
    rows = conn.execute(
        "SELECT faults FROM swing_analyses ORDER BY date DESC, id DESC LIMIT 3"
    ).fetchall()
    counts: dict[str, int] = {}
    for r in rows:
        for tag in json.loads(r["faults"] or "[]"):
            counts[tag] = counts.get(tag, 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])


def _handicap_and_trend(conn):
    rows = conn.execute(
        "SELECT score, course_rating, slope FROM rounds ORDER BY date DESC, id DESC LIMIT 20"
    ).fetchall()
    diffs = [(113.0 / r["slope"]) * (r["score"] - r["course_rating"]) for r in rows]
    result = compute_handicap_index(diffs)
    trend = None
    if len(diffs) >= 6:
        half = min(5, len(diffs) // 2)
        recent = sum(diffs[:half]) / half
        prior = sum(diffs[half:half * 2]) / half
        trend = recent - prior  # negative == improving (lower differentials)
    return result, trend


# --------------------------------------------------------------------------- #
# selection helpers
# --------------------------------------------------------------------------- #
def _drills_for_category(cat):
    return [d for d in DRILLS if cat in d["sg_categories"]]


def _drills_for_fault(tag):
    return [d for d in DRILLS if tag in d["fault_tags"]]


def _mobility_for_fault(tag):
    return [m for m in MOBILITY if tag in m["fault_tags"]]


def _pretty(text: str) -> str:
    return text.replace("-", " ").title()


def _select_warmup(fault_tags: list[str], used_ids: set) -> list:
    picked = []
    for tag in fault_tags:
        for m in _mobility_for_fault(tag):
            if m["id"] not in used_ids:
                picked.append(m)
                used_ids.add(m["id"])
        if len(picked) >= 2:
            break
    if not picked:  # default warm-up
        picked = [MOBILITY[0]]
        used_ids.add(MOBILITY[0]["id"])
    return picked[:2]


# --------------------------------------------------------------------------- #
# command
# --------------------------------------------------------------------------- #
def run(args: argparse.Namespace) -> int:
    conn = db.connect(args.db)
    sg = _sg_by_category(conn, args.days)
    faults = _recent_faults(conn)
    hcp, trend = _handicap_and_trend(conn)

    if not sg and not faults and hcp is None:
        print("Not enough data yet to build a tailored plan.")
        print("Log rounds (`round add`), shots (`sg log`), and a swing (`analyze`),")
        print("then re-run `train` — it adapts to your actual leaks and faults.")
        print("\nMeanwhile, a balanced starter session:")
        for d in (DRILLS[2], DRILLS[4], DRILLS[0]):  # wedges, putting, tee
            print(f"  • {d['name']} — {d['minutes']} min")
        return 0

    # Build the priority-ordered list of needs.
    needs = []  # (kind, key, info)
    leaks = sorted(
        ((c, per) for c, (per, _n) in sg.items() if per < LEAK_THRESHOLD),
        key=lambda x: x[1],  # most negative first
    )
    for cat, per in leaks:
        needs.append(("sg", cat, per))
    for tag, count in faults:
        needs.append(("fault", tag, count))
    if not needs and hcp is not None:
        idx = hcp["index"]
        cats = (["short-game", "putting", "approach"] if idx >= 20
                else ["approach", "short-game", "putting"] if idx >= 10
                else ["approach", "putting"])
        for c in cats:
            needs.append(("hcp", c, idx))

    fault_tags = [t for t, _ in faults]
    budget = args.minutes
    used_ids: set = set()
    warmup = _select_warmup(fault_tags, used_ids)
    base_min = sum(m["minutes"] for m in warmup)
    used_min = base_min

    def candidates(need):
        kind, key, _ = need
        if kind in ("sg", "hcp"):
            cand = _drills_for_category(key)
            # Most specific first: category is the drill's primary tag, then
            # fewer categories overall, then shorter.
            cand = sorted(cand, key=lambda d: (d["sg_categories"].index(key),
                                               len(d["sg_categories"]), d["minutes"]))
        else:
            cand = sorted(_drills_for_fault(key), key=lambda d: d["minutes"])
        return cand

    cand_lists = [candidates(need) for need in needs]
    chosen: dict[int, list] = {i: [] for i in range(len(needs))}

    # Breadth-first: round 0 gives each need its most-specific drill (top
    # priority first); round 1 adds a second drill to each, again from the
    # top. So lower-priority leaks aren't starved by the worst one.
    for depth in range(MAX_DRILLS_PER_NEED):
        if used_min >= budget:
            break
        for i in range(len(needs)):
            if len(chosen[i]) > depth:
                continue
            for d in cand_lists[i]:
                if d["id"] in used_ids:
                    continue
                forced = used_min == base_min and not any(chosen.values())
                if used_min + d["minutes"] <= budget or forced:
                    chosen[i].append(d)
                    used_ids.add(d["id"])
                    used_min += d["minutes"]
                break  # one drill per (need, depth) attempt
            if used_min >= budget:
                break

    blocks = [(needs[i], chosen[i]) for i in range(len(needs)) if chosen[i]]

    # ----- render -----
    print(f"Your training plan — {args.minutes} min target")
    sources = []
    if sg:
        sources.append(f"strokes gained (last {args.days} days)")
    if faults:
        sources.append("swing faults (recent analysis)")
    if hcp is not None:
        sources.append("handicap trend")
    print(f"Built from: {', '.join(sources) if sources else 'starter defaults'}.\n")

    if hcp is not None:
        line = f"Handicap Index: {hcp['index']:.1f}"
        if trend is not None:
            direction = ("trending down" if trend < -0.1 else
                         "trending up" if trend > 0.1 else "holding steady")
            line += f"  ({direction} {trend:+.1f} vs your prior rounds)"
        print(line + "\n")

    for i, (need, drills) in enumerate(blocks, 1):
        kind, key, info = need
        if kind == "sg":
            reason = f"{_pretty(key)} — losing {abs(info):.1f} shots/round"
        elif kind == "fault":
            reason = f"{_pretty(key)} — flagged by swing analysis"
        else:
            reason = f"{_pretty(key)} — common weak spot at your handicap"
        print(f"Priority {i} — {reason}")
        for d in drills:
            print(f"  • {d['name']} — {d['minutes']} min")
            print(f"      {d['why']}")
        print()

    print("Warm-up (do this first):")
    for m in warmup:
        print(f"  • {m['name']} — {m['minutes']} min")
        print(f"      {m['why']}")

    print(f"\nPlanned time: {used_min} min"
          + (f" (of {budget} budget)" if used_min <= budget else
             f" (over {budget} budget — trim the last item)"))
    print("Re-run `train` after you log new rounds, shots, or a swing — "
          "the plan adapts.")
    return 0
