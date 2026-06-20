"""``course`` — a personal course book (Phase 2, built).

Save per-hole notes and geometry (par, length, fairway width, trouble,
forced carry) one course at a time. The geometry doubles as saved input
for ``strategy tee --course X --hole N``, so you describe a hole once and
replay the strategy recommendation without re-typing the flags.

* ``course hole``  add or update a hole (course auto-created; merges, so
                   you can add a note later without re-entering par/length)
* ``course show``  print the whole book, or one hole in full detail
* ``course list``  list saved courses with par/yardage totals
"""

from __future__ import annotations

import argparse

from .. import db


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("course", help="Personal course book (notes + geometry)")
    sub = p.add_subparsers(dest="course_command", metavar="<subcommand>")
    sub.required = True

    h = sub.add_parser("hole", help="Add or update a hole")
    h.add_argument("--course", required=True, help="Course name")
    h.add_argument("--hole", type=int, required=True, help="Hole number")
    h.add_argument("--par", type=int, choices=(3, 4, 5))
    h.add_argument("--length", type=float, help="Yards from tee to pin")
    h.add_argument("--fairway-width", type=float, help="Fairway width (yards)")
    h.add_argument("--ob-left", type=float, help="Penalty/OB yards left of center")
    h.add_argument("--ob-right", type=float, help="Penalty/OB yards right of center")
    h.add_argument("--forced-carry", type=float, help="Must-carry distance (yards)")
    h.add_argument("--note", help="Free-text note for the hole")
    h.set_defaults(func=run_hole)

    s = sub.add_parser("show", help="Show a course book (or one hole)")
    s.add_argument("--course", required=True, help="Course name")
    s.add_argument("--hole", type=int, default=None, help="Show just this hole")
    s.set_defaults(func=run_show)

    lst = sub.add_parser("list", help="List saved courses")
    lst.set_defaults(func=run_list)


# --------------------------------------------------------------------------- #
# shared helpers (also used by the strategy command)
# --------------------------------------------------------------------------- #
def get_course(conn, name):
    return conn.execute(
        "SELECT * FROM courses WHERE name = ? COLLATE NOCASE", (name.strip(),)
    ).fetchone()


def load_hole(conn, course_name, hole_num):
    """Saved hole row (or None) joined across courses, case-insensitive."""
    return conn.execute(
        "SELECT ch.* FROM course_holes ch JOIN courses c ON c.id = ch.course_id "
        "WHERE c.name = ? COLLATE NOCASE AND ch.hole = ?",
        (course_name.strip(), hole_num),
    ).fetchone()


def _get_or_create_course(conn, name):
    row = get_course(conn, name)
    if row:
        return row["id"], row["name"]
    with conn:
        cur = conn.execute("INSERT INTO courses (name) VALUES (?)", (name.strip(),))
    return cur.lastrowid, name.strip()


# --------------------------------------------------------------------------- #
# course hole (upsert/merge)
# --------------------------------------------------------------------------- #
def run_hole(args: argparse.Namespace) -> int:
    conn = db.connect(args.db)
    cid, cname = _get_or_create_course(conn, args.course)
    existing = conn.execute(
        "SELECT * FROM course_holes WHERE course_id = ? AND hole = ?",
        (cid, args.hole),
    ).fetchone()

    keys = ("par", "length", "fairway_width", "ob_left", "ob_right",
            "forced_carry", "note")
    provided = {
        "par": args.par, "length": args.length, "fairway_width": args.fairway_width,
        "ob_left": args.ob_left, "ob_right": args.ob_right,
        "forced_carry": args.forced_carry, "note": args.note,
    }
    # Merge: a provided value wins; otherwise keep what's already saved.
    merged = {k: (provided[k] if provided[k] is not None
                  else (existing[k] if existing else None)) for k in keys}

    with conn:
        conn.execute(
            "INSERT INTO course_holes (course_id, hole, par, length, fairway_width, "
            "ob_left, ob_right, forced_carry, note) VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(course_id, hole) DO UPDATE SET par=excluded.par, "
            "length=excluded.length, fairway_width=excluded.fairway_width, "
            "ob_left=excluded.ob_left, ob_right=excluded.ob_right, "
            "forced_carry=excluded.forced_carry, note=excluded.note",
            (cid, args.hole, merged["par"], merged["length"], merged["fairway_width"],
             merged["ob_left"], merged["ob_right"], merged["forced_carry"],
             merged["note"]),
        )
    verb = "Updated" if existing else "Saved"
    bits = []
    if merged["par"]:
        bits.append(f"par {merged['par']}")
    if merged["length"]:
        bits.append(f"{merged['length']:.0f} yds")
    print(f"{verb} {cname} hole {args.hole}"
          + (f" ({', '.join(bits)})" if bits else "") + ".")
    if merged["length"]:
        print(f"  Strategy: python -m scratch strategy tee "
              f"--course \"{cname}\" --hole {args.hole}")
    return 0


# --------------------------------------------------------------------------- #
# course show
# --------------------------------------------------------------------------- #
def _trouble_str(h) -> str:
    bits = []
    if h["fairway_width"] is not None:
        bits.append(f"fw {h['fairway_width']:.0f}y")
    if h["ob_left"] is not None:
        bits.append(f"OB {h['ob_left']:.0f}L")
    if h["ob_right"] is not None:
        bits.append(f"OB {h['ob_right']:.0f}R")
    if h["forced_carry"] is not None:
        bits.append(f"carry {h['forced_carry']:.0f}")
    return ", ".join(bits)


def run_show(args: argparse.Namespace) -> int:
    conn = db.connect(args.db)
    course = get_course(conn, args.course)
    if course is None:
        print(f"No course named {args.course!r}. List courses with "
              "`course list`, add a hole with `course hole`.")
        return 0

    if args.hole is not None:
        h = conn.execute(
            "SELECT * FROM course_holes WHERE course_id = ? AND hole = ?",
            (course["id"], args.hole),
        ).fetchone()
        if h is None:
            print(f"{course['name']} hole {args.hole} not saved yet.")
            return 0
        print(f"{course['name']} — hole {h['hole']}")
        if h["par"] or h["length"]:
            print(f"  par {h['par'] or '?'}, {h['length'] or '?'} yds")
        trouble = _trouble_str(h)
        if trouble:
            print(f"  {trouble}")
        if h["note"]:
            print(f"  note: {h['note']}")
        return 0

    holes = conn.execute(
        "SELECT * FROM course_holes WHERE course_id = ? ORDER BY hole",
        (course["id"],),
    ).fetchall()
    if not holes:
        print(f"{course['name']} has no holes saved yet. Add one with "
              "`course hole --course ... --hole N --par 4 --length 410`.")
        return 0

    total_par = sum(h["par"] or 0 for h in holes)
    total_yds = sum(h["length"] or 0 for h in holes)
    print(f"{course['name']}  ({len(holes)} hole(s), par {total_par}, "
          f"{total_yds:.0f} yds)\n")
    print(f"  {'#':>2}  {'Par':>3}  {'Yds':>4}  Notes / trouble")
    print("  " + "-" * 50)
    for h in holes:
        detail = h["note"] or _trouble_str(h) or ""
        yds = f"{h['length']:.0f}" if h["length"] else ""
        print(f"  {h['hole']:>2}  {str(h['par'] or ''):>3}  {yds:>4}  {detail[:44]}")
    return 0


def run_list(args: argparse.Namespace) -> int:
    conn = db.connect(args.db)
    rows = conn.execute(
        "SELECT c.name, COUNT(ch.id) AS holes, "
        "COALESCE(SUM(ch.par),0) AS par, COALESCE(SUM(ch.length),0) AS yds "
        "FROM courses c LEFT JOIN course_holes ch ON ch.course_id = c.id "
        "GROUP BY c.id ORDER BY c.name"
    ).fetchall()
    if not rows:
        print("No courses saved yet. Add a hole with:")
        print('  python -m scratch course hole --course "PGA National" '
              "--hole 1 --par 4 --length 410")
        return 0
    for r in rows:
        print(f"  {r['name']}  —  {r['holes']} hole(s), par {r['par']}, "
              f"{r['yds']:.0f} yds")
    return 0
