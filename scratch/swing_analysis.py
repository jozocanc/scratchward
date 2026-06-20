"""Swing-analysis engine: pose extraction, key-position detection,
biomechanical metrics, and annotated rendering.

This module imports the heavy computer-vision stack (OpenCV, MediaPipe,
NumPy) at import time, so it is imported lazily by the ``analyze`` command
— the rest of the CLI runs without these packages installed.

Pose uses the MediaPipe **Tasks** ``PoseLandmarker`` API (the legacy
``mp.solutions.pose`` API is absent from current builds). It needs a model
bundle, downloaded once to the data dir by :func:`ensure_model`.

The pipeline:

    ensure_model(dir)        -> path to the .task model (downloads if absent)
    extract_pose(video, m)   -> per-frame 33-landmark arrays + video meta
    detect_key_positions(..) -> address / top / impact frame indices
    compute_metrics(..)      -> tempo, X-factor, head movement, spine
    render_outputs(..)       -> annotated .mp4 + key stills on disk

Detection is heuristic and based on the trajectory of the hands (wrists):
hands rise to the top of the backswing (their highest point), then return
toward the ball at impact. Everything downstream keys off those frames.
"""

from __future__ import annotations

import math
import os

os.environ.setdefault("GLOG_minloglevel", "2")  # quiet mediapipe/absl chatter

import urllib.request
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision

# BlazePose 33-landmark skeleton topology (the legacy solutions API that
# exposed POSE_CONNECTIONS is not available, so we hardcode it).
POSE_CONNECTIONS = frozenset([
    (0, 1), (1, 2), (2, 3), (3, 7), (0, 4), (4, 5), (5, 6), (6, 8), (9, 10),
    (11, 12), (11, 13), (13, 15), (15, 17), (15, 19), (15, 21), (17, 19),
    (12, 14), (14, 16), (16, 18), (16, 20), (16, 22), (18, 20),
    (11, 23), (12, 24), (23, 24), (23, 25), (24, 26), (25, 27), (26, 28),
    (27, 29), (29, 31), (27, 31), (28, 30), (30, 32), (28, 32),
])

POSE_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
    "pose_landmarker_full/float16/latest/pose_landmarker_full.task"
)
POSE_MODEL_FILENAME = "pose_landmarker_full.task"

# MediaPipe Pose landmark indices we use.
NOSE = 0
L_SH, R_SH = 11, 12
L_WR, R_WR = 15, 16
L_HIP, R_HIP = 23, 24
L_ANK, R_ANK = 27, 28

# Reference ranges (heuristic, documented in the report and README).
TEMPO_IDEAL = (2.5, 3.5)     # backswing:downswing ratio, benchmark ~3:1
TEMPO_FAST = 2.3             # below this: rushed
TEMPO_SLOW = 3.8             # above this: sluggish transition
XFACTOR_STRONG = 35.0        # degrees of shoulder-hip separation at top
XFACTOR_LOW = 25.0
HEAD_SWAY_PCT = 8.0          # head movement as % of body height
HEAD_GOOD_PCT = 5.0
SPINE_CHANGE_OK = 8.0        # spine-angle change address->impact, degrees
SPINE_CHANGE_BAD = 12.0

LOW_FPS_WARN = 60.0          # below this, tempo timing is coarse


# --------------------------------------------------------------------------- #
# model + pose extraction
# --------------------------------------------------------------------------- #
def model_path(model_dir) -> Path:
    return Path(model_dir) / POSE_MODEL_FILENAME


def ensure_model(model_dir) -> Path:
    """Return the pose-model path, downloading it once if it's missing."""
    path = model_path(model_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        tmp = path.with_suffix(".task.part")
        urllib.request.urlretrieve(POSE_MODEL_URL, tmp)
        tmp.replace(path)
    return path


def extract_pose(video_path: str | Path, model_file: str | Path) -> tuple[dict, list]:
    """Run MediaPipe PoseLandmarker over every frame.

    Returns (meta, frames) where meta has fps/width/height/n_frames and
    frames[i] is a list of 33 (x, y, z, visibility) tuples in normalized
    [0, 1] coordinates, or None if no body was detected in that frame.
    """
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"could not open video: {video_path}")
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    eff_fps = fps if fps > 0 else 30.0

    options = mp_vision.PoseLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=str(model_file)),
        running_mode=mp_vision.RunningMode.VIDEO,
        num_poses=1,
    )

    frames: list = []
    last_ts = -1
    with mp_vision.PoseLandmarker.create_from_options(options) as landmarker:
        i = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            ts = int(i * 1000 / eff_fps)
            if ts <= last_ts:
                ts = last_ts + 1
            last_ts = ts
            res = landmarker.detect_for_video(mp_image, ts)
            if res.pose_landmarks:
                lms = res.pose_landmarks[0]
                frames.append(
                    [(p.x, p.y, p.z, getattr(p, "visibility", 1.0) or 1.0) for p in lms]
                )
            else:
                frames.append(None)
            i += 1
    cap.release()
    meta = {"fps": fps, "width": width, "height": height, "n_frames": len(frames)}
    return meta, frames


# --------------------------------------------------------------------------- #
# small geometry helpers (operate on the frames list)
# --------------------------------------------------------------------------- #
def _pt(frames, i, idx) -> tuple[float, float]:
    lm = frames[i]
    if lm is None:
        return (math.nan, math.nan)
    p = lm[idx]
    return (p[0], p[1])


def _mid(frames, i, a, b) -> tuple[float, float]:
    ax, ay = _pt(frames, i, a)
    bx, by = _pt(frames, i, b)
    return ((ax + bx) / 2, (ay + by) / 2)


def _line_angle(frames, i, a, b) -> float:
    ax, ay = _pt(frames, i, a)
    bx, by = _pt(frames, i, b)
    return math.degrees(math.atan2(by - ay, bx - ax))


def _rot_y(frames, i, a, b) -> float:
    """Angle of the a->b segment about the vertical axis, in the horizontal
    (x, z) plane, using MediaPipe's depth (z) coordinate. This captures real
    body rotation that a flat 2D image angle can't — a turning shoulder line
    barely changes its 2D angle but sweeps through depth.
    """
    lm = frames[i]
    if lm is None:
        return math.nan
    pa, pb = lm[a], lm[b]
    return math.degrees(math.atan2(pb[2] - pa[2], pb[0] - pa[0]))


def _spine_angle(frames, i) -> float:
    """Angle of the hip->shoulder line from vertical, in degrees."""
    sx, sy = _mid(frames, i, L_SH, R_SH)
    hx, hy = _mid(frames, i, L_HIP, R_HIP)
    # Image y grows downward; "up" is -y. 0 deg == perfectly vertical.
    return math.degrees(math.atan2(sx - hx, -(sy - hy)))


def _ang_diff(a: float, b: float) -> float:
    """Signed smallest difference a-b, wrapped to [-180, 180]."""
    return (a - b + 180.0) % 360.0 - 180.0


def _hand_y(frames) -> np.ndarray:
    ys = []
    for i in range(len(frames)):
        _, ly = _pt(frames, i, L_WR)
        _, ry = _pt(frames, i, R_WR)
        ys.append(np.nanmean([ly, ry]))
    return np.array(ys, dtype=float)


def _hand_x(frames) -> np.ndarray:
    xs = []
    for i in range(len(frames)):
        lx, _ = _pt(frames, i, L_WR)
        rx, _ = _pt(frames, i, R_WR)
        xs.append(np.nanmean([lx, rx]))
    return np.array(xs, dtype=float)


def _fill(a: np.ndarray) -> np.ndarray:
    a = a.copy()
    good = ~np.isnan(a)
    if good.sum() == 0:
        return a
    idx = np.arange(len(a))
    a[~good] = np.interp(idx[~good], idx[good], a[good])
    return a


def _smooth(a: np.ndarray, k: int = 5) -> np.ndarray:
    if len(a) < k or k < 2:
        return a
    # Pad with edge values (not zeros) so the swing's start/finish frames
    # aren't biased toward 0 — otherwise the follow-through can be mistaken
    # for the top of the backswing.
    pad = k // 2
    padded = np.pad(a, pad, mode="edge")
    return np.convolve(padded, np.ones(k) / k, mode="valid")


# --------------------------------------------------------------------------- #
# key-position detection
# --------------------------------------------------------------------------- #
def detect_key_positions(frames, meta) -> dict | None:
    """Find address, top, and impact frame indices from hand height.

    Returns None if there isn't enough pose data to find a swing.
    """
    detected = sum(1 for f in frames if f is not None)
    if detected < 3 or len(frames) < 3:
        return None

    hand_y = _smooth(_fill(_hand_y(frames)), 7)
    hand_x = _smooth(_fill(_hand_x(frames)), 7)
    if np.all(np.isnan(hand_y)):
        return None
    n = len(hand_y)
    last = n - 1

    # Top of backswing: hands at their highest (smallest y) — a robust global pick.
    top = int(np.argmin(hand_y))

    # Frame-to-frame hand speed — drives the address (still) detection. Using
    # motion (not elapsed time) keeps detection correct on slow-motion clips,
    # which is how swings are usually filmed.
    dx = np.diff(hand_x, prepend=hand_x[0])
    dy = np.diff(hand_y, prepend=hand_y[0])
    speed = _smooth(np.sqrt(dx * dx + dy * dy), 7)

    # Address and impact share a hand height: the hands sit at the ball before
    # the takeaway and return there at impact. Use a robust "hands low" height
    # (high percentile of hand_y), then walk OUT from the top in both directions
    # to the first frame at that height. Address = start of the takeaway (not
    # the still setup or waggle, which is what inflated the backswing and skewed
    # tempo); impact = hands back at the ball (not the finish). Timing-free.
    addr_h = float(np.nanpercentile(hand_y, 88))
    a = top
    while a > 0 and hand_y[a] < addr_h * 0.97:
        a -= 1
    address = a
    im = top
    while im < last and hand_y[im] < addr_h * 0.97:
        im += 1
    if im >= last:                       # never returned: fall back to speed peak
        im = top + 1 + int(np.argmax(speed[top + 1:])) if top + 1 < n else last
    impact = im

    if address >= top:
        address = max(0, top - 1)
    if impact <= top:
        impact = min(top + 1, last)

    # Confidence: a trimmed single swing puts the top in the middle of the clip
    # with a real backswing and downswing on either side. If the top lands at
    # the very start/end, or either phase is tiny, the clip is likely untrimmed
    # or cut off, and the metrics can't be trusted.
    confident = (n * 0.08 <= top <= n * 0.92) and (top - address) >= 3 and (impact - top) >= 3

    return {
        "address": address,
        "top": top,
        "impact": impact,
        "detected_frames": detected,
        "confident": confident,
    }


# --------------------------------------------------------------------------- #
# metrics
# --------------------------------------------------------------------------- #
def compute_metrics(frames, keys, meta, view: str) -> tuple[dict, list]:
    """Compute tempo, X-factor, head movement, and spine consistency.

    Returns (metrics, faults). ``faults`` is a list of stable tags that
    line up with the drill library so the trainer can consume them.
    """
    a, t, im = keys["address"], keys["top"], keys["impact"]

    back, down = t - a, im - t
    tempo = back / down if down > 0 else math.nan

    # X-factor: shoulder rotation minus hip rotation from address to top,
    # measured about the vertical axis using depth (z) — real 3D rotation, not
    # a flat image angle that wraps past 180 deg on a big turn.
    sh_turn = _ang_diff(_rot_y(frames, t, L_SH, R_SH), _rot_y(frames, a, L_SH, R_SH))
    hp_turn = _ang_diff(_rot_y(frames, t, L_HIP, R_HIP), _rot_y(frames, a, L_HIP, R_HIP))
    x_factor = abs(_ang_diff(sh_turn, hp_turn))

    # Head movement address->impact, normalized to body height.
    nax, nay = _pt(frames, a, NOSE)
    nix, niy = _pt(frames, im, NOSE)
    _, may = _mid(frames, a, L_ANK, R_ANK)
    body = abs(may - nay)
    if not body or math.isnan(body) or body < 1e-6:
        # Fall back to a torso-based proxy if ankles aren't visible.
        _, mhy = _mid(frames, a, L_HIP, R_HIP)
        body = abs(mhy - nay) * 2 or 1.0
    head_total = math.hypot(nix - nax, niy - nay) / body * 100
    head_lateral = abs(nix - nax) / body * 100
    head_vertical = abs(niy - nay) / body * 100

    # Spine-angle consistency: change from address to impact.
    spine_addr = _spine_angle(frames, a)
    spine_imp = _spine_angle(frames, im)
    spine_change = abs(_ang_diff(spine_imp, spine_addr))

    metrics = {
        "tempo_ratio": None if math.isnan(tempo) else round(tempo, 2),
        "tempo_back_frames": back,
        "tempo_down_frames": down,
        "x_factor": round(x_factor, 1),
        "shoulder_turn": round(sh_turn, 1),
        "hip_turn": round(hp_turn, 1),
        "head_movement": round(head_total, 1),
        "head_lateral": round(head_lateral, 1),
        "head_vertical": round(head_vertical, 1),
        "spine_address": round(spine_addr, 1),
        "spine_impact": round(spine_imp, 1),
        "spine_change": round(spine_change, 1),
    }

    faults: list[str] = []
    if not math.isnan(tempo):
        if tempo < TEMPO_FAST:
            faults.append("fast-tempo")
        elif tempo > TEMPO_SLOW:
            faults.append("slow-tempo")
    if x_factor < XFACTOR_LOW:
        faults.append("low-x-factor")
    # Lateral sway is the meaningful head metric face-on; total works DTL.
    head_metric = head_lateral if view == "face-on" else head_total
    if head_metric > HEAD_SWAY_PCT:
        faults.append("head-sway")
    if spine_change > SPINE_CHANGE_BAD:
        faults.append("spine-loss")
    return metrics, faults


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #
# BGR colors for the annotated overlays.
_COL = {
    "panel": (34, 30, 26), "white": (245, 245, 245), "dim": (180, 180, 180),
    "ok": (95, 200, 120), "warn": (60, 190, 240), "flag": (72, 80, 235),
    "accent": (200, 180, 45), "accent2": (60, 165, 250),
    "limbL": (235, 185, 70), "limbR": (70, 165, 250),
    "torso": (236, 236, 236), "face": (150, 150, 150), "joint": (255, 255, 255),
}
_FONT = cv2.FONT_HERSHEY_SIMPLEX
_LEFT = {11, 13, 15, 17, 19, 21, 23, 25, 27, 29, 31}
_RIGHT = {12, 14, 16, 18, 20, 22, 24, 26, 28, 30, 32}
_FACE_IDX = set(range(0, 11))
_TORSO_CONN = frozenset({(11, 12), (23, 24), (11, 23), (12, 24)})
_TITLES = {"ADDRESS": "ADDRESS", "TOP": "TOP OF BACKSWING", "IMPACT": "IMPACT"}


def _phase(i: int, keys: dict) -> str:
    if i < keys["address"]:
        return "Setup"
    if i < keys["top"]:
        return "Backswing"
    if i < keys["impact"]:
        return "Downswing"
    return "Follow-through"


def _blend_rect(img, x1, y1, x2, y2, color, alpha) -> None:
    h, w = img.shape[:2]
    x1, y1, x2, y2 = max(0, x1), max(0, y1), min(w, x2), min(h, y2)
    if x2 <= x1 or y2 <= y1:
        return
    roi = img[y1:y2, x1:x2]
    overlay = roi.copy()
    overlay[:] = color
    cv2.addWeighted(overlay, alpha, roi, 1 - alpha, 0, roi)


def _text(img, s, org, scale, color, thick=1) -> None:
    cv2.putText(img, s, (org[0] + 1, org[1] + 1), _FONT, scale, (12, 12, 12),
                thick, cv2.LINE_AA)
    cv2.putText(img, s, org, _FONT, scale, color, thick, cv2.LINE_AA)


def _seg_color(a, b):
    key = (min(a, b), max(a, b))
    if key in _TORSO_CONN:
        return _COL["torso"]
    if a in _FACE_IDX and b in _FACE_IDX:
        return _COL["face"]
    if a in _LEFT and b in _LEFT:
        return _COL["limbL"]
    if a in _RIGHT and b in _RIGHT:
        return _COL["limbR"]
    return _COL["torso"]


def _draw_skeleton(frame, lm, w, h, thick) -> None:
    pts = [(int(p[0] * w), int(p[1] * h)) for p in lm]
    vis = [p[3] for p in lm]
    for a, b in POSE_CONNECTIONS:
        if a < len(pts) and b < len(pts) and vis[a] > 0.3 and vis[b] > 0.3:
            cv2.line(frame, pts[a], pts[b], _seg_color(a, b), thick, cv2.LINE_AA)
    for idx, (x, y) in enumerate(pts):
        if vis[idx] > 0.3:
            cv2.circle(frame, (x, y), thick + 1, _COL["joint"], -1, cv2.LINE_AA)
            cv2.circle(frame, (x, y), thick + 1, (40, 40, 40), 1, cv2.LINE_AA)


def _px(lm, idx, w, h):
    p = lm[idx]
    return (int(p[0] * w), int(p[1] * h))


def _mid_px(lm, a, b, w, h):
    ax, ay = _px(lm, a, w, h)
    bx, by = _px(lm, b, w, h)
    return ((ax + bx) // 2, (ay + by) // 2)


def _glow_line(frame, p1, p2, color, thick) -> None:
    cv2.line(frame, p1, p2, (20, 20, 20), thick + 4, cv2.LINE_AA)
    cv2.line(frame, p1, p2, color, thick, cv2.LINE_AA)


def _rows(label, metrics, view):
    """(label, value, status) rows for a key position, status drives color."""
    if label == "TOP":
        xf = metrics["x_factor"]
        return [
            ("X-factor", f"{xf:.0f} deg", "ok" if xf >= XFACTOR_LOW else "flag"),
            ("turn", f"sh {metrics['shoulder_turn']:+.0f}  hip {metrics['hip_turn']:+.0f}", "dim"),
        ]
    if label == "IMPACT":
        hm = metrics["head_lateral"] if view == "face-on" else metrics["head_movement"]
        hst = "ok" if hm <= HEAD_GOOD_PCT else "warn" if hm <= HEAD_SWAY_PCT else "flag"
        sc = metrics["spine_change"]
        sst = "ok" if sc <= SPINE_CHANGE_OK else "warn" if sc <= SPINE_CHANGE_BAD else "flag"
        return [("Head move", f"{hm:.1f}% body", hst), ("Spine change", f"{sc:.0f} deg", sst)]
    tr = metrics["tempo_ratio"]
    if tr is None:
        trow = ("Tempo", "n/a", "dim")
    else:
        trow = ("Tempo", f"{tr:.1f} : 1", "ok" if TEMPO_FAST <= tr <= TEMPO_SLOW else "flag")
    return [trow, ("Spine @ addr", f"{metrics['spine_address']:.0f} deg", "dim")]


def _draw_card(img, label, metrics, view, s) -> None:
    rows = _rows(label, metrics, view)
    title_sc, row_sc = 0.8 * s, 0.6 * s
    pad, line_h = int(14 * s), int(30 * s)
    x0, y0 = int(14 * s), int(52 * s)           # sits below the header bar
    sized = [(_TITLES[label], title_sc, 2)] + [(f"{l}: {v}", row_sc, 2) for l, v, _ in rows]
    width = max(cv2.getTextSize(t, _FONT, sc, th)[0][0] for t, sc, th in sized) + int(40 * s)
    height = pad * 2 + int(20 * s) + line_h * len(rows)
    _blend_rect(img, x0, y0, x0 + width, y0 + height, _COL["panel"], 0.6)
    _blend_rect(img, x0, y0, x0 + int(6 * s), y0 + height, _COL["accent"], 0.95)
    x, y = x0 + int(20 * s), y0 + int(30 * s)
    _text(img, _TITLES[label], (x, y), title_sc, _COL["white"], 2)
    for lab, val, st in rows:
        y += line_h
        _text(img, f"{lab}:", (x, y), row_sc, _COL["dim"], 1)
        (lw, _), _ = cv2.getTextSize(f"{lab}:  ", _FONT, row_sc, 1)
        _text(img, val, (x + lw, y), row_sc, _COL.get(st, _COL["white"]), 2)


def _draw_highlights(frame, lm, label, w, h, thick) -> None:
    if label in ("ADDRESS", "IMPACT"):
        _glow_line(frame, _mid_px(lm, L_HIP, R_HIP, w, h),
                   _mid_px(lm, L_SH, R_SH, w, h), _COL["accent"], thick + 1)
    if label == "TOP":
        _glow_line(frame, _px(lm, L_SH, w, h), _px(lm, R_SH, w, h), _COL["accent"], thick + 1)
        _glow_line(frame, _px(lm, L_HIP, w, h), _px(lm, R_HIP, w, h), _COL["accent2"], thick + 1)
    if label == "IMPACT":
        cv2.circle(frame, _px(lm, NOSE, w, h), thick + 4, _COL["accent2"], 2, cv2.LINE_AA)


def render_outputs(video_path, frames, keys, metrics, meta, out_dir,
                   view="down-the-line") -> dict:
    """Write an annotated video and key stills; return their paths."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    w, h = meta["width"], meta["height"]
    fps = meta["fps"] or 30.0
    s = max(0.7, min(2.2, h / 720))          # scale overlays to resolution
    thick = max(2, round(h / 240))

    cap = cv2.VideoCapture(str(video_path))
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    out_video = out_dir / "annotated.mp4"
    writer = cv2.VideoWriter(str(out_video), fourcc, fps, (w, h))

    keymap = {keys["address"]: "ADDRESS", keys["top"]: "TOP", keys["impact"]: "IMPACT"}
    stills: dict[str, Path] = {}
    bar = int(40 * s)
    bh = int(46 * s)

    i = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        lm = frames[i] if i < len(frames) else None
        if lm is not None:
            _draw_skeleton(frame, lm, w, h, thick)
        _blend_rect(frame, 0, 0, w, bar, _COL["panel"], 0.45)
        _text(frame, _phase(i, keys), (int(14 * s), int(28 * s)), 0.7 * s, _COL["white"], 2)
        _text(frame, f"frame {i}", (w - int(150 * s), int(28 * s)), 0.55 * s, _COL["dim"], 1)
        _text(frame, "scratch - swing analysis",
              (w - int(250 * s), h - int(14 * s)), 0.5 * s, _COL["dim"], 1)

        if i in keymap:
            label = keymap[i]
            still = frame.copy()
            if lm is not None:
                _draw_highlights(still, lm, label, w, h, thick)
                if label == "IMPACT":
                    addr_lm = frames[keys["address"]]
                    if addr_lm is not None:
                        an, cn = _px(addr_lm, NOSE, w, h), _px(lm, NOSE, w, h)
                        cv2.circle(still, an, thick + 3, _COL["dim"], 1, cv2.LINE_AA)
                        _glow_line(still, an, cn, _COL["accent2"], thick)
            for img in (still, frame):  # banner on both the still and the video
                _blend_rect(img, 0, h - bh, w, h, _COL["panel"], 0.55)
                _text(img, _TITLES[label], (int(14 * s), h - int(16 * s)),
                      0.8 * s, _COL["accent"], 2)
            _draw_card(still, label, metrics, view, s)
            path = out_dir / f"{label.lower()}.png"
            cv2.imwrite(str(path), still)
            stills[label] = path
        writer.write(frame)
        i += 1
    cap.release()
    writer.release()
    return {"video": out_video, "stills": stills}
