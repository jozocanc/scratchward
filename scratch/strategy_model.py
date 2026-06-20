"""On-course strategy engine — pure, testable, no CLI or DB.

Given a club's real distance + dispersion (from logged shots) and a simple
hole description, this simulates where the ball actually finishes and uses
the strokes-gained baseline (``data/sg_baseline``) to score the outcome:

    expected score for a tee shot
        = 1 (the tee shot)
          + penalty cost when the shot finds trouble
          + expected strokes to hole out from where it ends up

averaged over a Monte-Carlo of the club's two-dimensional dispersion
(longitudinal carry spread + lateral spread, shifted by the player's
natural miss bias). It searches a few aim lines per club and returns the
club + aim that minimizes expected score — the DECADE-style "play to your
dispersion, not your best strike" idea, grounded in your own data.

Common random numbers (one shared set of standard-normal draws, seeded)
are reused across every club/aim so comparisons are low-variance and the
recommendation is deterministic.
"""

from __future__ import annotations

import math
import random

from .data.sg_baseline import expected_strokes

AIM_OFFSETS = range(-15, 16, 5)   # yards; negative = aim left, positive = right
N_SAMPLES = 1500


def _dispersion(stat: dict) -> tuple[float, float, float, float]:
    """(mean carry, longitudinal std, lateral std, lateral bias) for a club.

    Falls back to sensible spreads when a club has little/no side data:
    ~3% of carry long, ~6% lateral, floored so it's never zero.
    """
    mean = stat["mean"]
    long_std = stat["std"] if stat.get("n", 0) > 1 and stat["std"] > 0 else 0.03 * mean
    side_std = stat.get("side_std", 0.0)
    lat_std = max(side_std if side_std > 0 else 0.06 * mean, 3.0)
    bias = stat.get("side_mean", 0.0)  # natural miss: + right / - left
    return mean, long_std, lat_std, bias


def make_samples(n: int = N_SAMPLES, seed: int = 42):
    """Shared standard-normal (z_long, z_lat) draws for common random numbers."""
    rng = random.Random(seed)
    return [(rng.gauss(0, 1), rng.gauss(0, 1)) for _ in range(n)]


def simulate(stat: dict, hole: dict, aim: float, samples) -> dict:
    """Expected score + outcome rates for one club aimed at `aim` yards."""
    mean, long_std, lat_std, bias = _dispersion(stat)
    length = hole["length"]
    fw_half = hole["fairway_half"]
    ob_left = hole.get("ob_left")
    ob_right = hole.get("ob_right")
    forced = hole.get("forced_carry")

    total = 0.0
    penalties = 0
    fairways = 0
    leave_sum = 0.0
    for z_long, z_lat in samples:
        carry = mean + z_long * long_std
        lat = aim + bias + z_lat * lat_std
        remaining = math.hypot(length - carry, lat)
        leave_sum += remaining

        penalty = (
            (ob_right is not None and lat > ob_right)
            or (ob_left is not None and lat < -ob_left)
            or (forced is not None and carry < forced)
        )
        if penalty:
            penalties += 1
            # Drop, one stroke penalty, play on from the rough.
            total += 1 + 1 + expected_strokes(max(remaining, 10.0), "rough")
        else:
            in_fairway = abs(lat) <= fw_half
            fairways += in_fairway
            lie = "fairway" if in_fairway else "rough"
            total += 1 + expected_strokes(max(remaining, 1.0), lie)

    n = len(samples)
    return {
        "expected": total / n,
        "penalty_rate": penalties / n,
        "fairway_rate": fairways / n,
        "avg_leave": leave_sum / n,
    }


def recommend(clubs: dict, hole: dict, seed: int = 42, n: int = N_SAMPLES) -> list:
    """Rank clubs by best-aim expected score (lowest first).

    `clubs` maps club name -> stat dict (from dispersion.club_stats). Each
    result carries the best aim and that aim's outcome rates.
    """
    samples = make_samples(n, seed)
    results = []
    for name, stat in clubs.items():
        best = None
        for aim in AIM_OFFSETS:
            r = simulate(stat, hole, aim, samples)
            if best is None or r["expected"] < best["expected"]:
                best = dict(r, aim=aim)
        results.append(dict(best, club=name))
    results.sort(key=lambda r: r["expected"])
    return results
