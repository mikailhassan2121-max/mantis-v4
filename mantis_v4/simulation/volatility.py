"""
MANTIS V4 — volatility estimator comparison (Phase 5 section 10).

sigma_remaining is the single most consequential input Normal-Z has. It sits in
the denominator of z, so an estimator that runs 20% low inflates every z by 25%
and pushes probabilities toward the extremes across the whole book. Phase 4's
sensitivity work makes the same point analytically: vega is large exactly where
the probability is interesting.

So the choice of estimator is not a detail — and section 10 is explicit that it
must NOT be made on raw accuracy:

    "Do not pick the volatility model based on raw accuracy only.
     Prioritize Brier, log loss, calibration, robustness."

Accuracy is nearly blind to it. Scaling sigma by any positive constant leaves
the SIGN of z unchanged, so the predicted side never moves and accuracy is
almost invariant. What changes is how extreme the probabilities are, which is
precisely what Brier, log loss and the calibration slope measure. An estimator
comparison judged on accuracy would find almost nothing and conclude, wrongly,
that the choice does not matter.

ESTIMATORS

    validated   0.70 * sd(20 contiguous) + 0.30 * sd(60)   <- Phase 3/4 anchor
    short       sd of the last 5 contiguous 1-minute returns
    medium      sd of the last 15
    long        sd of the last 30
    ewma        EWMA standard deviation, span 20
    blend_sl    0.5 * short + 0.5 * long
    mad         1.4826 * median absolute deviation (robust to jumps)
    regime      validated, scaled by a short/long volatility ratio

All are computed causally from the precomputed indicator frames; none reads a
future bar.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np
import pandas as pd

MIN_SIGMA = 1e-9

# MAD -> sigma consistency factor for a normal distribution.
MAD_SCALE = 1.4826


def mad_volatility(frame: pd.DataFrame, window: int = 20) -> pd.Series:
    """Robust volatility via median absolute deviation of contiguous returns.

    Far less sensitive to a single jump bar than a standard deviation, which is
    the point: one 3-sigma minute can inflate an sd-based estimate for the next
    20 minutes and suppress every z-score in that span.
    """
    closes = frame["Close"].astype(float)
    log_returns = np.log(closes / closes.shift(1))
    deltas = frame.index.to_series().diff().dt.total_seconds()
    adjacent = (deltas - 60.0).abs() <= 5.0
    masked = log_returns.where(adjacent)

    median = masked.rolling(window, min_periods=max(5, window // 2)).median()
    deviation = (masked - median).abs()
    return MAD_SCALE * deviation.rolling(window, min_periods=max(5, window // 2)).median()


@dataclass
class VolatilityEstimator:
    """One named way of turning bar history into a per-minute sigma."""

    name: str
    description: str
    #: Given the precomputed base/extended frames, return a per-bar sigma_1m.
    build: Callable[[pd.DataFrame, pd.DataFrame, pd.DataFrame], pd.Series]


def build_estimators() -> list[VolatilityEstimator]:
    """The section 10 comparison set."""

    def validated(frame, base, ext):
        # Exactly the Phase 3/4 anchor: 0.70 * vol20 + 0.30 * vol60.
        return base["realized_vol_1m"]

    def short(frame, base, ext):
        return ext["rv_5m"]

    def medium(frame, base, ext):
        return ext["rv_15m"]

    def long(frame, base, ext):
        return ext["rv_30m"]

    def ewma(frame, base, ext):
        return ext["rv_ewma"]

    def blend_short_long(frame, base, ext):
        return 0.5 * ext["rv_5m"] + 0.5 * ext["rv_30m"]

    def mad(frame, base, ext):
        return mad_volatility(frame, window=20)

    def regime_adjusted(frame, base, ext):
        # Nudge the validated estimate toward the current short/long ratio,
        # bounded so a single volatile stretch cannot double or halve sigma.
        ratio = (ext["rv_5m"] / ext["rv_30m"].replace(0, np.nan)).clip(0.5, 2.0)
        return base["realized_vol_1m"] * np.sqrt(ratio.fillna(1.0))

    return [
        VolatilityEstimator("validated", "0.70*sd(20) + 0.30*sd(60) — Phase 3/4 anchor", validated),
        VolatilityEstimator("short_5m", "sd of last 5 contiguous returns", short),
        VolatilityEstimator("medium_15m", "sd of last 15", medium),
        VolatilityEstimator("long_30m", "sd of last 30", long),
        VolatilityEstimator("ewma_20", "EWMA sd, span 20", ewma),
        VolatilityEstimator("blend_short_long", "0.5*short + 0.5*long", blend_short_long),
        VolatilityEstimator("mad_robust", "1.4826 * MAD, window 20", mad),
        VolatilityEstimator("regime_adjusted", "validated * sqrt(clipped short/long)", regime_adjusted),
    ]


def sigma_remaining_from(sigma_1m: np.ndarray, seconds_remaining: np.ndarray) -> np.ndarray:
    """sigma_1m * sqrt(minutes remaining) — the anchor's own scaling rule."""
    sigma_1m = np.maximum(np.asarray(sigma_1m, dtype="float64"), MIN_SIGMA)
    minutes = np.maximum(np.asarray(seconds_remaining, dtype="float64") / 60.0, 0.0)
    return np.maximum(sigma_1m * np.sqrt(minutes), MIN_SIGMA)


@dataclass
class EstimatorReport:
    name: str
    description: str
    n: int
    coverage: float                 # fraction of rows the estimator could serve
    brier: float
    logloss: float
    accuracy: float
    calibration_slope: float
    calibration_intercept: float
    ece: float
    mean_sigma_1m: float
    median_z_abs: float
    #: Ratio of realised terminal dispersion to predicted sigma. 1.0 means the
    #: estimator is correctly scaled; <1 means it OVERSTATES volatility.
    realised_over_predicted: float

    def as_row(self) -> dict:
        return {
            "name": self.name, "description": self.description, "n": self.n,
            "coverage": self.coverage, "brier": self.brier, "logloss": self.logloss,
            "accuracy": self.accuracy,
            "calibration_slope": self.calibration_slope,
            "calibration_intercept": self.calibration_intercept,
            "ece": self.ece, "mean_sigma_1m": self.mean_sigma_1m,
            "median_z_abs": self.median_z_abs,
            "realised_over_predicted": self.realised_over_predicted,
        }
