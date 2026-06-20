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

    hand_y = _smooth(_fill(_hand_y(frames)), 5)
    if np.all(np.isnan(hand_y)):
        return None

    top = int(np.argmin(hand_y))  # highest hands == smallest y
    address = 0 if top <= 0 else int(np.argmax(hand_y[: top + 1]))
    addr_level = hand_y[address]

    # Impact: after the top, the first frame where hands return to ~address
    # height (coming back down to the ball).
    impact = None
    for j in range(1, len(hand_y) - top):
        if hand_y[top + j] >= addr_level * 0.97:
            impact = top + j
            break
    if impact is None:
        tail = hand_y[top:]
        impact = top + (int(np.argmin(np.abs(tail - addr_level))) if len(tail) > 1 else 1)

    # Keep ordering sane and in-bounds: address < top < impact <= last.
    last = len(hand_y) - 1
    if top <= address:
        top = min(address + 1, last)
    impact = min(impact, last)
    if impact <= top:
        impact = min(top + 1, last)

    return {
        "address": address,
        "top": top,
        "impact": impact,
        "detected_frames": detected,
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

    # X-factor: change in shoulder-line angle minus change in hip-line angle
    # from address to top. A 2D estimate from a single camera.
    sh_turn = _ang_diff(_line_angle(frames, t, L_SH, R_SH),
                        _line_angle(frames, a, L_SH, R_SH))
    hp_turn = _ang_diff(_line_angle(frames, t, L_HIP, R_HIP),
                        _line_angle(frames, a, L_HIP, R_HIP))
    x_factor = abs(sh_turn - hp_turn)

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
def _phase(i: int, keys: dict) -> str:
    if i < keys["address"]:
        return "setup"
    if i < keys["top"]:
        return "backswing"
    if i < keys["impact"]:
        return "downswing"
    return "follow-through"


def _draw_skeleton(frame, lm, w, h) -> None:
    pts = [(int(p[0] * w), int(p[1] * h)) for p in lm]
    for a, b in POSE_CONNECTIONS:
        if a < len(pts) and b < len(pts):
            cv2.line(frame, pts[a], pts[b], (0, 200, 0), 2)
    for x, y in pts:
        cv2.circle(frame, (x, y), 3, (0, 0, 255), -1)


def _still_lines(label: str, metrics: dict) -> list[str]:
    if label == "TOP":
        return [
            f"X-factor: {metrics['x_factor']:.0f} deg",
            f"(shoulders {metrics['shoulder_turn']:+.0f}, hips {metrics['hip_turn']:+.0f})",
        ]
    if label == "IMPACT":
        return [
            f"Head move: {metrics['head_movement']:.1f}% body ht",
            f"Spine change: {metrics['spine_change']:.0f} deg",
        ]
    # ADDRESS
    tr = metrics["tempo_ratio"]
    return [
        f"Tempo: {tr:.1f}:1" if tr else "Tempo: n/a",
        f"Spine @ address: {metrics['spine_address']:.0f} deg",
    ]


def render_outputs(video_path, frames, keys, metrics, meta, out_dir) -> dict:
    """Write an annotated video and key stills; return their paths."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    w, h = meta["width"], meta["height"]
    fps = meta["fps"] or 30.0

    cap = cv2.VideoCapture(str(video_path))
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    out_video = out_dir / "annotated.mp4"
    writer = cv2.VideoWriter(str(out_video), fourcc, fps, (w, h))

    keymap = {keys["address"]: "ADDRESS", keys["top"]: "TOP", keys["impact"]: "IMPACT"}
    stills: dict[str, Path] = {}
    font = cv2.FONT_HERSHEY_SIMPLEX

    i = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        lm = frames[i] if i < len(frames) else None
        if lm is not None:
            _draw_skeleton(frame, lm, w, h)
        cv2.putText(frame, f"{_phase(i, keys)}  f{i}", (12, 30), font, 0.8,
                    (255, 255, 255), 2, cv2.LINE_AA)
        if i in keymap:
            label = keymap[i]
            cv2.putText(frame, label, (12, h - 24), font, 1.0, (0, 255, 255), 2,
                        cv2.LINE_AA)
            still = frame.copy()
            y = 64
            for line in _still_lines(label, metrics):
                cv2.putText(still, line, (12, y), font, 0.7, (0, 255, 255), 2,
                            cv2.LINE_AA)
                y += 30
            path = out_dir / f"{label.lower()}.png"
            cv2.imwrite(str(path), still)
            stills[label] = path
        writer.write(frame)
        i += 1
    cap.release()
    writer.release()
    return {"video": out_video, "stills": stills}
