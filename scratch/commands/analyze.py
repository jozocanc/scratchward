"""``analyze`` — swing analyzer. Fully implemented.

Extracts pose from a swing video, detects address/top/impact, computes
tempo / X-factor / head movement / spine consistency, prints a plain-text
report flagging deviations from reference ranges, and writes an annotated
video plus key still frames to disk (the one command that produces files
rather than pure text). Results persist in the ``swing_analyses`` table so
the trainer can read the faults.

The heavy CV stack (OpenCV, MediaPipe, NumPy) is imported lazily here so
the rest of the CLI works without it installed.
"""

from __future__ import annotations

import argparse
import json
from datetime import date as date_cls
from pathlib import Path

from .. import db


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("analyze", help="Analyze a swing video")
    p.add_argument("video", help="Path to a swing video (e.g. .mp4/.mov)")
    p.add_argument(
        "--view",
        choices=("down-the-line", "face-on"),
        required=True,
        help="Camera angle — selects which metrics are most reliable",
    )
    p.set_defaults(func=run)


def _flag(ok: bool) -> str:
    return "OK  " if ok else "FLAG"


def _report(meta, keys, metrics, faults, view, sa) -> None:
    fps = meta["fps"]
    print(f"\nSwing analysis — {view}")
    print(
        f"Source: {fps:.0f} fps, {meta['width']}x{meta['height']}, "
        f"{meta['n_frames']} frames "
        f"({keys['detected_frames']} with a body detected)"
    )
    if fps and fps < sa.LOW_FPS_WARN:
        print(
            f"  ! Low frame rate ({fps:.0f} fps). Tempo timing is coarse — "
            f"120+ fps is ideal for swing capture."
        )
    print(
        f"\nKey positions:  address=f{keys['address']}  "
        f"top=f{keys['top']}  impact=f{keys['impact']}\n"
    )

    # Tempo
    tr = metrics["tempo_ratio"]
    if tr is not None:
        ok = sa.TEMPO_FAST <= tr <= sa.TEMPO_SLOW
        print(f"  {_flag(ok)}  Tempo (back:down)      {tr:.1f} : 1     "
              f"[ideal ~3:1, {sa.TEMPO_IDEAL[0]}-{sa.TEMPO_IDEAL[1]}]  "
              f"({metrics['tempo_back_frames']}f / {metrics['tempo_down_frames']}f)")
    else:
        print("  ----  Tempo                  n/a (couldn't separate down/through)")

    # X-factor
    xf = metrics["x_factor"]
    ok = xf >= sa.XFACTOR_LOW
    print(f"  {_flag(ok)}  X-factor at top        {xf:.0f} deg       "
          f"[strong >{sa.XFACTOR_STRONG:.0f}, low <{sa.XFACTOR_LOW:.0f}]  (2D estimate)")

    # Head movement
    hm = metrics["head_movement"]
    hl = metrics["head_lateral"]
    primary = hl if view == "face-on" else hm
    ok = primary <= sa.HEAD_SWAY_PCT
    which = "lateral" if view == "face-on" else "total"
    print(f"  {_flag(ok)}  Head move (addr->imp)  {primary:.1f}% body ht  "
          f"[good <{sa.HEAD_GOOD_PCT:.0f}%, sway >{sa.HEAD_SWAY_PCT:.0f}%]  ({which})")

    # Spine
    sc = metrics["spine_change"]
    ok = sc <= sa.SPINE_CHANGE_OK
    print(f"  {_flag(ok)}  Spine-angle change     {sc:.0f} deg       "
          f"[consistent <{sa.SPINE_CHANGE_OK:.0f}, loss >{sa.SPINE_CHANGE_BAD:.0f}]  "
          f"(addr {metrics['spine_address']:.0f} -> imp {metrics['spine_impact']:.0f})")

    # View-specific reliability note.
    if view == "down-the-line":
        print("\n  View note: spine angle and head depth read best down-the-line; "
              "treat X-factor and lateral sway as rough.")
    else:
        print("\n  View note: lateral sway and tempo read best face-on; "
              "treat spine angle as rough from the front.")

    if faults:
        print(f"\n  Faults flagged: {', '.join(faults)}")
        print("  -> feed the trainer: python -m scratch train")
    else:
        print("\n  No major faults flagged.")


def run(args: argparse.Namespace) -> int:
    video = Path(args.video).expanduser()
    if not video.exists():
        raise SystemExit(f"error: video not found: {video}")

    try:
        from .. import swing_analysis as sa
    except ImportError as exc:
        print("Swing analysis needs opencv-python, mediapipe, and numpy.")
        print("  Install:  pip install -r requirements.txt   "
              "(or  pip install -e \".[analyze]\")")
        print(f"  (import error: {exc})")
        return 1

    # Pose model lives next to the database; downloaded once on first use.
    model_dir = db.resolve_db_path(args.db).parent / "models"
    if not sa.model_path(model_dir).exists():
        print("Fetching pose model (~9 MB, first run only) ...")
    model_file = sa.ensure_model(model_dir)

    print(f"Analyzing {video.name} ... (extracting pose, this can take a moment)")
    meta, frames = sa.extract_pose(video, model_file)
    keys = sa.detect_key_positions(frames, meta)
    if keys is None:
        print("Could not detect a full swing — too few frames with a body in them.")
        print("Make sure the golfer is fully in frame and well lit, "
              "and the clip covers address through impact.")
        return 1

    metrics, faults = sa.compute_metrics(frames, keys, meta, args.view)
    _report(meta, keys, metrics, faults, args.view, sa)

    # Render annotated outputs next to the database.
    base = db.resolve_db_path(args.db).parent / "analysis"
    out_dir = base / video.stem
    n = 2
    while out_dir.exists():
        out_dir = base / f"{video.stem}-{n}"
        n += 1
    outputs = sa.render_outputs(video, frames, keys, metrics, meta, out_dir)

    # Persist.
    conn = db.connect(args.db)
    with conn:
        conn.execute(
            "INSERT INTO swing_analyses (date, video_path, view, fps, tempo_ratio, "
            "x_factor, head_movement, spine_consistency, faults, output_path) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                date_cls.today().isoformat(),
                str(video),
                args.view,
                meta["fps"],
                metrics["tempo_ratio"],
                metrics["x_factor"],
                metrics["head_movement"],
                metrics["spine_change"],
                json.dumps(faults),
                str(out_dir),
            ),
        )

    print(f"\nAnnotated video: {outputs['video']}")
    stills = ", ".join(str(p) for p in outputs["stills"].values())
    if stills:
        print(f"Key stills: {stills}")
    return 0
