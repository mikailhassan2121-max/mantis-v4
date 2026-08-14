"""
MANTIS V4 — fragility score (Phase 5 section 6).

WHAT FRAGILITY IS, AND WHAT IT IS NOT

Fragility answers: *how much should I distrust this probability estimate?*
It does NOT answer: *which way will it resolve?*

The two must stay separate, and section 6 says so directly:

    "Do not let fragility arbitrarily alter probability.
     It should influence trade eligibility / abstention later."

So ``FragilityScore`` never touches ``p_yes``. It is computed alongside the
probability and consumed later, by Phase 7's entry logic, as an eligibility
gate. A 91% probability with EXTREME fragility stays 91% — it simply may not
qualify to be traded.

That separation is the whole design. A system that quietly shaded probabilities
by a confidence heuristic would destroy the calibration Phase 4 established
(holdout slope 1.011, ECE 0.0098) and would make the resulting numbers unusable
for EV once contract prices arrive.

COMPONENTS

Each component is mapped to [0, 1] where 1 is maximally fragile, then combined
by a weighted mean into a 0-100 score. The weights are DECLARED, not fitted --
Phase 5 must not tune anything on outcomes, and a fitted fragility score would
be a second probability model in disguise.

    gamma          curvature of p in spot: how violently p moves per 1% tick
    vega           dependence of p on the volatility estimate
    theta          rate of mechanical change as time drains
    proximity      closeness to the reference in z units
    time           how little time remains to recover from an adverse move
    crossings      how often the path has already flipped sides
    vol_of_vol     instability of the volatility estimate itself
    disagreement   spread across independent probability channels

Whether this score has any empirical value is an open question that Phase 5
TESTS rather than assumes -- see the fragility report. If filtering by it does
not improve selective accuracy, it stays a diagnostic and nothing more.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

BANDS = ("LOW", "MEDIUM", "HIGH", "EXTREME")

# Declared, not fitted. See the module docstring.
DEFAULT_WEIGHTS: dict[str, float] = {
    "gamma": 0.20,
    "vega": 0.15,
    "theta": 0.10,
    "proximity": 0.20,
    "time": 0.10,
    "crossings": 0.10,
    "vol_of_vol": 0.05,
    "disagreement": 0.10,
}

# Band edges on the 0-100 score. Also declared, not tuned on outcomes.
BAND_EDGES = (25.0, 50.0, 75.0)


@dataclass
class FragilityScore:
    score: np.ndarray                     # 0-100, higher = more fragile
    band: np.ndarray                      # LOW / MEDIUM / HIGH / EXTREME
    components: dict[str, np.ndarray] = field(default_factory=dict)
    weights: dict[str, float] = field(default_factory=dict)

    def validate(self) -> "FragilityScore":
        s = np.asarray(self.score, dtype="float64")
        if not np.all(np.isfinite(s)):
            raise ValueError("fragility score contains non-finite values")
        if np.any(s < 0.0) or np.any(s > 100.0):
            raise ValueError("fragility score outside [0, 100]")
        return self

    def summary(self) -> dict:
        return {
            "mean": float(np.mean(self.score)),
            "median": float(np.median(self.score)),
            "bands": {b: int((self.band == b).sum()) for b in BANDS},
        }


def _squash(x: np.ndarray, scale: float) -> np.ndarray:
    """Map |x| into [0, 1) with a saturating curve.

    ``x / (|x| + scale)`` rather than a hard clip, so that extreme values keep
    ordering instead of all collapsing onto 1.0. ``scale`` is the value at
    which the component reads 0.5.
    """
    a = np.abs(np.asarray(x, dtype="float64"))
    a = np.nan_to_num(a, nan=0.0, posinf=1e18, neginf=0.0)
    return a / (a + scale)


def _robust_scale(values: np.ndarray, fallback: float) -> float:
    """Median absolute value, used to self-calibrate a component's scale.

    Computed from the rows being scored, which is legitimate: fragility is a
    relative diagnostic, not a probability, and it is never fitted against
    outcomes. Falls back to a declared constant when the sample is degenerate.
    """
    a = np.abs(np.asarray(values, dtype="float64"))
    a = a[np.isfinite(a)]
    if len(a) == 0:
        return fallback
    median = float(np.median(a))
    return median if median > 0 else fallback


def compute_fragility(
    *,
    gamma_per_pct2: np.ndarray,
    vega_per_10pct_vol: np.ndarray,
    theta_per_30s: np.ndarray,
    abs_z: np.ndarray,
    seconds_remaining: np.ndarray,
    crossings: Optional[np.ndarray] = None,
    vol_of_vol: Optional[np.ndarray] = None,
    disagreement: Optional[np.ndarray] = None,
    weights: Optional[dict[str, float]] = None,
    scales: Optional[dict[str, float]] = None,
) -> FragilityScore:
    """Combine the components into a 0-100 fragility score."""
    weights = dict(weights or DEFAULT_WEIGHTS)
    scales = dict(scales or {})

    n = len(np.asarray(abs_z, dtype="float64"))
    components: dict[str, np.ndarray] = {}

    components["gamma"] = _squash(
        gamma_per_pct2, scales.get("gamma", _robust_scale(gamma_per_pct2, 0.05))
    )
    components["vega"] = _squash(
        vega_per_10pct_vol, scales.get("vega", _robust_scale(vega_per_10pct_vol, 0.02))
    )
    components["theta"] = _squash(
        theta_per_30s, scales.get("theta", _robust_scale(theta_per_30s, 0.01))
    )

    # Proximity: |z| small means the outcome is close to a coin flip and a
    # small move flips it. |z| = 1 reads 0.5.
    z = np.abs(np.nan_to_num(np.asarray(abs_z, dtype="float64"), nan=0.0))
    components["proximity"] = 1.0 / (1.0 + z)

    # Time: little time left means little chance to recover from an adverse
    # move. 150 seconds reads 0.5.
    seconds = np.maximum(np.asarray(seconds_remaining, dtype="float64"), 0.0)
    components["time"] = 150.0 / (150.0 + seconds)

    if crossings is not None:
        c = np.nan_to_num(np.asarray(crossings, dtype="float64"), nan=0.0)
        components["crossings"] = _squash(c, 2.0)
    else:
        components["crossings"] = np.zeros(n, dtype="float64")

    if vol_of_vol is not None:
        components["vol_of_vol"] = _squash(
            vol_of_vol, scales.get("vol_of_vol", _robust_scale(vol_of_vol, 0.3))
        )
    else:
        components["vol_of_vol"] = np.zeros(n, dtype="float64")

    if disagreement is not None:
        d = np.nan_to_num(np.asarray(disagreement, dtype="float64"), nan=0.0)
        # 0.05 of probability spread across channels reads 0.5.
        components["disagreement"] = _squash(d, 0.05)
    else:
        components["disagreement"] = np.zeros(n, dtype="float64")

    active = {k: w for k, w in weights.items() if k in components}
    total_weight = sum(active.values())
    if total_weight <= 0:
        raise ValueError("fragility weights sum to zero")

    score = np.zeros(n, dtype="float64")
    for key, weight in active.items():
        score += weight * components[key]
    score = 100.0 * score / total_weight
    score = np.clip(score, 0.0, 100.0)

    band = np.full(n, BANDS[0], dtype=object)
    band[score >= BAND_EDGES[0]] = BANDS[1]
    band[score >= BAND_EDGES[1]] = BANDS[2]
    band[score >= BAND_EDGES[2]] = BANDS[3]

    return FragilityScore(
        score=score, band=band, components=components, weights=active
    ).validate()


def volatility_of_volatility(
    short_vol: np.ndarray, long_vol: np.ndarray
) -> np.ndarray:
    """|log(short/long)| — how unstable the volatility estimate currently is.

    A large value means the short and long windows disagree about how volatile
    the market is, so sigma_remaining (and therefore p) is being computed from
    a quantity that is itself in flux.
    """
    short = np.asarray(short_vol, dtype="float64")
    long = np.asarray(long_vol, dtype="float64")
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where((short > 0) & (long > 0), short / long, np.nan)
        out = np.abs(np.log(ratio))
    return np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)
