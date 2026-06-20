# Scratchward

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)
![Platform](https://img.shields.io/badge/Platform-macOS%20%7C%20Linux-lightgrey)
![Spec](https://img.shields.io/badge/spec-complete-brightgreen)
![Commands](https://img.shields.io/badge/commands-10-blue)

A terminal-first, all-in-one golf coaching tool. Text in, text out — the
only exception is swing analysis, which writes annotated video/stills to
disk and prints the path. Everything persists in a local SQLite database
so your data accumulates across rounds and sessions; the whole point is
the long-term picture.

> **Scratchward** — *toward scratch.* Run it as `scratch`
> (`python -m scratch`). The name lives in one constant — `APP_NAME` in
> `scratch/constants.py` — which drives the command, the help output, and
> the data location (`~/.scratch/`). Domain: **scratchward.com**.

## Quickstart

```bash
git clone git@github.com:jozocanc/scratchward.git
cd scratchward
python -m scratch round add --score 88 --rating 71.2 --slope 131
python -m scratch handicap            # your World Handicap System index
python -m scratch --help              # all 10 commands
```

The core needs **no third-party packages** — just Python 3.10+. Only the
swing analyzer needs extras (`pip install -r requirements.txt`).

## Contents

- [Status](#status) · [Setup](#setup) · [Data location](#data-location)
- [Commands](#commands) — handicap · strokes gained · practice · swing
  analyzer · trainer · goal · dispersion · strategy · course
- [Project layout](#project-layout) · [License](#license)

## Status

| Phase | Command | State |
|-------|---------|-------|
| 1 | `round add` / `round list` | ✅ built |
| 1 | `handicap` | ✅ built |
| 1 | `sg` (strokes gained) | ✅ built |
| 1 | `practice` | ✅ built |
| 1 | `analyze` (swing) | ✅ built |
| 1 | `train` | ✅ built |
| 2 | `goal` (handicap goal tracking) | ✅ built |
| 2 | `dispersion` (club distances) | ✅ built |
| 2 | `strategy` (on-course) | ✅ built |
| 2 | `course` (course notes) | ✅ built |

**The full spec is built — Phase 1 and all of Phase 2.** The modules
connect end to end: swing faults and strokes-gained leaks feed the
trainer; the practice log checks whether the work paid off; goal tracking
turns a target Index into category work; dispersion gives the reliable
distances; on-course strategy plans tee shots off that dispersion using
the same strokes-gained baseline; and the course book stores each hole so
strategy can replay it by name.

## Setup

Requires Python 3.10+. The core commands (handicap, strokes gained,
practice, trainer) need **no third-party packages** — just clone and run.

```bash
cd ~/scratchward
python -m scratch --help
```

Optional: install it as a real `scratch` shell command:

```bash
pip install -e .
scratch --help
```

The swing analyzer needs computer-vision libraries. Install them only
when you want video analysis:

```bash
pip install -r requirements.txt        # opencv, mediapipe, numpy
# or:  pip install -e ".[analyze]"
```

## Data location

One SQLite file holds everything. Resolved in priority order:

1. `--db PATH` flag
2. `SCRATCH_DB` environment variable
3. default `~/.scratch/scratch.db`

The directory is created automatically on first run.

## Commands

### Handicap tracker (built)

Log rounds, then compute your Handicap Index.

```bash
# Log a round (date defaults to today; --course and --holes optional)
python -m scratch round add --score 88 --rating 71.2 --slope 131 --course "PGA National"
python -m scratch round add --score 84 --rating 70.5 --slope 128 --date 2026-06-10

# See your history (newest first)
python -m scratch round list
python -m scratch round list --limit 50

# Compute your Handicap Index
python -m scratch handicap
python -m scratch handicap --verbose   # show the differentials + table selection
```

**How the index is computed.** For each round, the score differential is
`(113 / slope) × (score − course_rating)`. From your most recent 20
rounds, the lowest *N* differentials are selected — where *N* and a small
adjustment come from the World Handicap System table (this is what handles
fewer than 20 rounds). The index is `mean(selected) × 0.96 + adjustment`,
rounded to one decimal and capped at 54.0. You need at least 3 rounds.

The selection table and the `0.96` factor are isolated in
`scratch/commands/handicap.py` (`WHS_TABLE`, `BONUS_FOR_EXCELLENCE`) so
the formula is trivial to adjust.

### Strokes gained (built)

Logs shot-level detail and compares each shot to expected-strokes
baselines by distance and lie, attributing gain/loss to off-the-tee,
approach, short-game, or putting.

**`SG = E(before) − E(after) − (1 + penalties)`** — how much better a shot
left you than the baseline expected, after paying for the stroke(s) used.
Positive = gained on the field. Each shot is attributed by its *starting*
position; par only matters to tell a par-3 tee shot (approach) from a
par-4/5 tee shot (off-the-tee).

> **Units:** distances are **yards** off the green and **feet** on the
> green (the baseline table matches). So `--start 20 --lie green` is a
> 20-foot putt.

**Interactive — fastest for a whole round.** The end of each shot
auto-fills the start of the next, and results use shorthand:

```bash
python -m scratch sg log --date 2026-06-18
```
```
Result shorthand:  'f 140' (fairway, 140y) · 'g 25' (green, 25ft) · 'h' (holed) · add '+1' for a penalty

Hole #1 [..]: 1
  Par [4]: 4
  Tee shot — distance to pin (yds): 400
    shot 1 from 400 yds (tee) → result: f 150     # ended fairway, 150y
    shot 2 from 150 yds (fairway) → result: g 20  # ended green, 20ft
    shot 3 from 20 ft (green) → result: g 3
    shot 4 from 3 ft (green) → result: h          # holed
```
Enter a blank hole number to finish. Add `+1` to any result for a penalty
stroke (e.g. `r 90 +1`). Link a session to a logged round with
`--round-id N`.

**One-liner — a single shot, scriptable.** End lie defaults to `green`;
use `--holed` to hole out, `--par 3` so a par-3 tee shot counts as
approach:

```bash
python -m scratch sg add --start 150 --lie fairway --end 20    # to 20 ft on the green
python -m scratch sg add --start 6 --lie green --holed         # made a 6-footer
python -m scratch sg add --start 420 --lie tee --end 150 --end-lie rough --par 4
```

**Report — where you're bleeding strokes.**

```bash
python -m scratch sg report --days 90
```
```
Strokes gained — last 90 days (12 round(s), 648 shots)

  off-the-tee   +0.30 / round   (  +3.6 total)
  approach      -2.90 / round   ( -34.8 total)   <- biggest leak
  short-game    -0.80 / round   (  -9.6 total)
  putting       -0.40 / round   (  -4.8 total)
  -----------
  TOTAL         -3.80 / round

  Inside 100 yds: -2.10 / round (approach + short game <=100y)

You're losing ~2.9 shots/round on approach. Practice that first.
```

The baseline table lives in `scratch/data/sg_baseline.py` (interpolated
between anchor distances) and the math in `scratch/strokes_gained.py` —
both easy to tune.

### Practice log + feedback loop (built)

Log what you worked on, then close the loop: `progress` compares your
strokes gained in a category **before** you started practicing it vs
**since**, so you can see whether the work actually moved the needle.

```bash
# Tag the focus to an SG category so it can be scored against results
python -m scratch practice add --focus approach --drills "wedge ladder" --duration 45
python -m scratch practice add --focus putting --duration 30 --date 2026-06-15
python -m scratch practice list

# The feedback loop
python -m scratch practice progress              # every focus you've practiced
python -m scratch practice progress --focus approach
python -m scratch practice progress --window 14  # only rounds within 14 days each side
```
```
Practice -> results feedback loop

approach   (3 session(s), 135 min, since 2026-05-20)
  drills: wedge ladder
  SG/round before: -2.90 (6 rd)     since: -1.40 (4 rd)     ^ improved +1.50
  -> The work is paying off. Keep going.
```

The "before" baseline is your SG in that category from rounds dated
before your first session on it; "since" is rounds played from that date
on. `--window N` limits both sides to N days around the start. A focus
that isn't an SG category (e.g. a swing-fault tag like `head-sway`) is
still logged and counted, but can't be scored against strokes gained —
the report says so and points you to the four categories.

### Swing analyzer (built)

The one command that writes files instead of text. Needs the CV extras
(`pip install -r requirements.txt`). On first run it downloads a ~9 MB
MediaPipe pose model to `~/.scratch/models/` (once).

```bash
python -m scratch analyze swing.mp4 --view down-the-line
python -m scratch analyze swing.mov --view face-on
```

It runs MediaPipe Pose (Tasks `PoseLandmarker`) over every frame, finds
**address / top / impact** from the hands' trajectory, and computes:

| Metric | What it measures | Reference |
|--------|------------------|-----------|
| **Tempo** | backswing : downswing frame ratio | ideal ~3 : 1 (2.5–3.5) |
| **X-factor** | shoulder-vs-hip turn at the top (2D estimate) | strong > 35°, low < 25° |
| **Head movement** | nose travel address→impact, % of body height | good < 5%, sway > 8% |
| **Spine angle** | change in spine tilt address→impact | consistent < 8°, loss > 12° |

It prints a plain-text report flagging each metric `OK`/`FLAG` against its
range, warns if the clip is low-fps (tempo timing needs ~120 fps to be
precise), and tailors which metrics are reliable to the `--view`. Sample:

```
Swing analysis — down-the-line
Source: 240 fps, 1080x1920, 96 frames (96 with a body detected)

Key positions:  address=f4  top=f64  impact=f84

  OK    Tempo (back:down)      2.9 : 1     [ideal ~3:1, 2.5-3.5]  (60f / 21f)
  OK    X-factor at top        38 deg      [strong >35, low <25]  (2D estimate)
  OK    Head move (addr->imp)  3.1% body ht  [good <5%, sway >8%]  (total)
  FLAG  Spine-angle change     13 deg      [consistent <8, loss >12]  (addr 34 -> imp 47)

  Faults flagged: spine-loss
  -> feed the trainer: python -m scratch train
```

It then writes an **annotated `.mp4`** (skeleton + phase labels) and three
**key stills** (`address.png`, `top.png`, `impact.png`, each with the
relevant metrics drawn on) to `~/.scratch/analysis/<clip>/`, and prints
the paths. The flagged faults (`fast-tempo`, `slow-tempo`, `low-x-factor`,
`head-sway`, `spine-loss`) persist to the database and feed the trainer.

> **Capture tips:** film one golfer, fully in frame, well lit, from a
> stable down-the-line or face-on angle, covering address through impact.
> The detection and metric ranges are pragmatic heuristics tuned for a
> single-camera phone clip — useful directional feedback, not launch-monitor
> precision. The thresholds live in `scratch/swing_analysis.py`.

### Trainer (built)

The capstone — it reads your real data and builds a prioritized practice
+ mobility routine inside a time budget.

```bash
python -m scratch train                 # 60-min plan
python -m scratch train --minutes 30    # tighter session
python -m scratch train --days 60       # SG window for leaks
```

Priority order (per the spec):

1. **Strokes-gained leaks** — worst category first (from `sg`)
2. **Swing faults** — from your latest `analyze` runs
3. **Handicap trend** — direction + level (from `round`); also drives a
   sensible default if you have rounds but no shots/swings logged

It selects from the built-in drill library (each drill tagged with the SG
category and/or fault it addresses), allocates breadth-first so every leak
gets a drill before any gets a second, picks the drill most *specific* to
each need, fits everything to `--minutes`, and adds a fault-matched
warm-up. Sample:

```
Your training plan — 60 min target
Built from: strokes gained (last 90 days), swing faults (recent analysis), handicap trend.

Handicap Index: 12.9  (trending down -4.3 vs your prior rounds)

Priority 1 — Putting — losing 1.0 shots/round
  • Lag putting ladder (20/30/40 ft) — 15 min
      Speed control to cut 3-putts.
Priority 2 — Approach — losing 0.8 shots/round
  • Trackman/eye 9-window approach control — 25 min
      Builds distance + flight control for scoring irons.
Priority 3 — Off The Tee — losing 0.3 shots/round
  • Shoulder-turn-over-stable-hips drill — 10 min
      Increases shoulder-hip separation at the top for more speed.

Warm-up (do this first):
  • Thoracic-spine rotations — 5 min
```

With no data logged yet, it prints a balanced starter session and tells
you what to log. The drill/mobility library lives in
`scratch/data/drills.py` — add your own, tagged by category/fault, and
the trainer will pick them up.

### Goal tracking (built — first Phase 2 feature)

Set a target Handicap Index; `goal status` turns the gap into concrete
work using your existing rounds + shots (no new logging needed).

```bash
python -m scratch goal set --handicap 10                 # open-ended, projects an ETA
python -m scratch goal set --handicap 10 --by 2026-12-31  # dated, judges on/behind pace
python -m scratch goal status
python -m scratch goal clear
```

It computes the strokes/round you need to gain (handicap points ≈
strokes/round), **distributes that gap across your current SG leaks** —
biggest leak absorbs the most — and projects a timeline from your recent
improvement rate. Sample:

```
Goal: reach a 10.0 Handicap Index
Current Index: 12.9   →   gap: +2.9 (improve)

You need to gain ~2.9 strokes/round. Where it comes from:
  • Approach      improve +2.4/round  (now -0.8 → +1.6)
  • Putting       improve +0.5/round  (now -0.2 → +0.3)
  Note: erasing all current leaks is ~1.0 strokes — the last 1.9 must come
  from raising your stronger areas above average.

At your recent rate (+2.3 strokes/month), ETA ~1 month → around 2026-07-28.
```

With a `--by` date it instead tells you the strokes/month you'd need and
whether your recent rate has you on pace. The same leaks drive `train`, so
the goal and your practice plan stay in sync.

### Shot dispersion / club distances (built — Phase 2)

Log carry per club, then get a **reliable** planning distance and the
**spread** per club, plus a gapping view that flags holes and overlaps in
your set.

```bash
# Bulk entry — club by club (fastest for a range/Trackman session)
python -m scratch dispersion log

# One-liner — a single shot; --side is yards offline (- left / + right)
python -m scratch dispersion add --club 7i --carry 155 --side -3

python -m scratch dispersion report          # last 365 days
python -m scratch dispersion report --days 30
```
```
Club distances & dispersion — last 365 days (18 shots)

  Club       n  Carry  Reliable          Spread        Side
  ---------------------------------------------------------
  Driver     5    264       256    ±9 (250-272)       4R ±4
  3W         3    242       239    ±4 (238-245)           -
  7I         7    154       150    ±4 (148-160)       0· ±5
  PW         3    121       118    ±3 (118-124)           -

Gapping (by reliable carry):
  Driver   256 yds
       │ 18 yd gap
  3W       239 yds
       │ 88 yd gap   <- large gap, consider filling
  7I       150 yds
       │ 32 yd gap   <- large gap, consider filling
  PW       118 yds
```

**Reliable** = the carry you beat ~80% of the time (20th-percentile once
you have ≥5 shots; mean − 0.85·std for small samples) — plan club
selection off that, not your one flush. **Side** shows your average miss
bias and its spread. Stats are in `club_stats` in
`scratch/commands/dispersion.py`. This is the data on-course strategy
plans from.

### On-course strategy (built — Phase 2)

Recommends the club + aim off a tee to **minimize expected score**, using
your real dispersion (from `dispersion`) and the strokes-gained baseline.
It Monte-Carlos where each club would actually finish — longitudinal carry
spread + lateral spread shifted by your natural miss bias — scores every
outcome (fairway / rough / penalty) by expected strokes to hole out, and
searches a handful of aim lines. Deterministic (seeded, common random
numbers), so the same hole always gives the same advice.

```bash
python -m scratch strategy tee --length 410 --par 4 --ob-right 24
python -m scratch strategy tee --length 410 --fairway-width 28 \
    --ob-right 24 --forced-carry 220
python -m scratch strategy approach --distance 150
```
```
Tee strategy — par 4, 410 yds
  fairway 32 yds wide; OB/penalty 24 yds right

   #  Club          Aim    Exp  Fairway  Penalty  Leave
  -----------------------------------------------------
   1  Driver         5L   4.04      87%       1%    146
   2  3W            ctr   4.10      99%       0%    170
   3  5W            ctr   4.21     100%       0%    186
   4  7I            ctr   4.49      91%       1%    256

Recommended: Driver, aim 5 yds left (expected 4.04, 146 yd leave).
```

Hole inputs: `--length` (to pin), `--par`, `--fairway-width`, optional
`--ob-left` / `--ob-right` (yards from center where penalty starts), and
`--forced-carry` (water/waste you must clear). `Aim` already bakes in your
miss bias — `5L` for a player who leaks right keeps the ball off the right
OB. `strategy approach --distance D` picks the shortest club that still
reliably carries the pin and shows your full club ladder. Engine:
`scratch/strategy_model.py`.

### Course book / notes (built — Phase 2)

A personal per-course, per-hole notebook. Saved geometry doubles as input
for `strategy tee`, so you describe a hole once and replay the advice by
name.

```bash
python -m scratch course hole --course "PGA National" --hole 1 \
    --par 4 --length 410 --ob-right 24 --note "Bail left; right is OB"
python -m scratch course hole --course "PGA National" --hole 1 \
    --note "Driver 5L is the play"          # merges — keeps par/length
python -m scratch course list
python -m scratch course show --course "PGA National"
python -m scratch course show --course "PGA National" --hole 1

# Replay strategy straight from the saved hole — no geometry flags:
python -m scratch strategy tee --course "PGA National" --hole 1
```
```
PGA National  (3 hole(s), par 12, 1125 yds)

   #  Par   Yds  Notes / trouble
  --------------------------------------------------
   1    4   410  Driver 5L is the play
   2    3   175  Back pin = sucker; play center
  18    5   540  Water carry off tee
```

`course hole` **merges** — pass only the fields you want to change and the
rest of the hole is preserved. Course names match case-insensitively.
Explicit `strategy tee` flags override saved values, so you can tweak a
front-pin length or extra trouble on the fly.

## Project layout

```
scratchward/             # project root
  scratch/               # the Python package (CLI command: `scratch`)
    constants.py         # APP_NAME — the single rename point
    cli.py               # argparse dispatcher
    db.py                # SQLite connection + schema
    strokes_gained.py    # SG engine (pure: classify + strokes_gained)
    swing_analysis.py    # pose extraction, metrics, rendering (CV deps)
    strategy_model.py    # on-course expected-score Monte-Carlo (pure)
    commands/            # one module per subcommand
      round.py  handicap.py  sg.py  practice.py  analyze.py
      train.py  goal.py  dispersion.py  strategy.py  course.py
    data/
      sg_baseline.py     # expected-strokes baseline table
      drills.py          # drill / mobility library
  requirements.txt
  pyproject.toml
  README.md
```

## License

MIT — see [LICENSE](LICENSE). © 2026 Jozo Cancar.
