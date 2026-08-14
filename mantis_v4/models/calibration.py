"""
MANTIS V4 — probability calibration (Phase 4 section 5).

THE QUESTION PHASE 4 EXISTS TO ANSWER

    "When MANTIS says an outcome has probability p, how close is that number to
     the observed frequency out of sample?"

Accuracy does not answer it. A model can be 85% accurate while every one of its
"90%" predictions wins only 72% of the time. That gap is invisible in an
accuracy number and fatal to an EV calculation, because EV is computed FROM the
probability. A miscalibrated probability produces a confidently wrong EV.

WHAT IS MEASURED

  Brier score            mean squared error of the forecasts
  log loss               negative log likelihood
  calibration intercept  logistic recalibration intercept; 0 is perfect
  calibration slope      logistic recalibration slope; 1 is perfect
                         slope < 1 means OVERCONFIDENT (spread too wide)
                         slope > 1 means UNDERCONFIDENT
  ECE                    expected calibration error, bucket-weighted
  MCE                    maximum calibration error, the worst bucket
  reliability data       per-bucket observed frequency with intervals

CALIBRATORS

  identity   no transformation, to see the raw model honestly
  Platt      sigmoid recalibration; one intercept, one slope; robust at low n
  isotonic   free-form monotone fit; more flexible, more prone to overfit,
             and it can produce steps that are artefacts of a small bucket

Section 5 requires the calibrator be CHOSEN ON VALIDATION DATA ONLY. The
selection helper here takes explicit fit and selection arrays so that it is
impossible to pass the same rows to both without noticing.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np

EPSILON = 1e-15

# Phase 4 section 5 names these buckets explicitly.
CONFIDENCE_BUCKETS = [
    (0.50, 0.55), (0.55, 0.60), (0.60, 0.65), (0.65, 0.70), (0.70, 0.75),
    (0.75, 0.80), (0.80, 0.85), (0.85, 0.90), (0.90, 0.95), (0.95, 1.001),
]


def brier_score(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((np.asarray(p, dtype="float64") - np.asarray(y, dtype="float64")) ** 2))


def log_loss(p: np.ndarray, y: np.ndarray) -> float:
    p = np.clip(np.asarray(p, dtype="float64"), EPSILON, 1 - EPSILON)
    y = np.asarray(y, dtype="float64")
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def calibration_intercept_slope(p: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Fit ``logit(observed) = a + b * logit(predicted)``.

    Perfect calibration is a = 0, b = 1. This is the standard
    calibration-in-the-large / calibration-slope decomposition.
    """
    import warnings

    from sklearn.linear_model import LogisticRegression

    p = np.clip(np.asarray(p, dtype="float64"), 1e-6, 1 - 1e-6)
    y = np.asarray(y, dtype="int64")
    if len(np.unique(y)) < 2:
        return float("nan"), float("nan")

    logit = np.log(p / (1 - p)).reshape(-1, 1)
    # See estimators.LogisticModel.fit for why `penalty` is retained and its
    # deprecation warning suppressed across the supported sklearn range.
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=FutureWarning)
        model = LogisticRegression(penalty=None, solver="lbfgs", max_iter=1000)
        model.fit(logit, y)
    return float(model.intercept_[0]), float(model.coef_[0][0])


@dataclass
class BucketStat:
    low: float
    high: float
    count: int
    mean_predicted: float
    observed_rate: float
    ci_low: float
    ci_high: float

    @property
    def gap(self) -> float:
        """Predicted minus observed. Positive means overconfident."""
        return self.mean_predicted - self.observed_rate


def wilson(successes: int, trials: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if trials <= 0:
        return float("nan"), float("nan")
    p = successes / trials
    den = 1.0 + z * z / trials
    centre = (p + z * z / (2 * trials)) / den
    margin = z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials)) / den
    return max(0.0, centre - margin), min(1.0, centre + margin)


def reliability_table(
    p: np.ndarray,
    y: np.ndarray,
    *,
    buckets: Sequence[tuple[float, float]] = CONFIDENCE_BUCKETS,
    fold_to_confidence: bool = True,
) -> list[BucketStat]:
    """Observed frequency per predicted-probability bucket.

    ``fold_to_confidence`` maps p < 0.5 to (1 - p) and flips the outcome, so a
    "90% NO" prediction lands in the 90-95% bucket alongside a "90% YES". That
    is the right view for a binary decision system: what matters is how often
    the side MANTIS would take actually wins.
    """
    p = np.asarray(p, dtype="float64")
    y = np.asarray(y, dtype="int64")

    if fold_to_confidence:
        take_yes = p >= 0.5
        confidence = np.where(take_yes, p, 1.0 - p)
        won = np.where(take_yes, y == 1, y == 0).astype("int64")
    else:
        confidence, won = p, y

    stats: list[BucketStat] = []
    for low, high in buckets:
        mask = (confidence >= low) & (confidence < high)
        n = int(mask.sum())
        if n == 0:
            stats.append(BucketStat(low, high, 0, float("nan"), float("nan"),
                                    float("nan"), float("nan")))
            continue
        k = int(won[mask].sum())
        lo, hi = wilson(k, n)
        stats.append(
            BucketStat(
                low=low, high=high, count=n,
                mean_predicted=float(confidence[mask].mean()),
                observed_rate=k / n, ci_low=lo, ci_high=hi,
            )
        )
    return stats


def expected_calibration_error(stats: Sequence[BucketStat]) -> tuple[float, float]:
    """(ECE, MCE) computed from a reliability table."""
    total = sum(s.count for s in stats)
    if total == 0:
        return float("nan"), float("nan")
    ece = 0.0
    mce = 0.0
    for s in stats:
        if s.count == 0 or not np.isfinite(s.mean_predicted):
            continue
        gap = abs(s.gap)
        ece += (s.count / total) * gap
        mce = max(mce, gap)
    return ece, mce


# ---------------------------------------------------------------------------
# Calibrators
# ---------------------------------------------------------------------------

class Calibrator(ABC):
    name: str = "calibrator"

    @abstractmethod
    def fit(self, p: np.ndarray, y: np.ndarray) -> "Calibrator":
        ...

    @abstractmethod
    def transform(self, p: np.ndarray) -> np.ndarray:
        ...

    def _check(self, out: np.ndarray) -> np.ndarray:
        out = np.clip(np.asarray(out, dtype="float64"), 0.0, 1.0)
        if not np.all(np.isfinite(out)):
            raise ValueError(f"{self.name} produced non-finite probabilities")
        return out


class IdentityCalibrator(Calibrator):
    """No transformation — shows the raw model as it actually is."""

    name = "uncalibrated"

    def fit(self, p, y) -> "IdentityCalibrator":
        return self

    def transform(self, p: np.ndarray) -> np.ndarray:
        return self._check(np.asarray(p, dtype="float64"))


class PlattCalibrator(Calibrator):
    """Sigmoid recalibration: one intercept and one slope on the logit."""

    name = "platt"

    def __init__(self) -> None:
        self._model = None

    def fit(self, p, y) -> "PlattCalibrator":
        import warnings

        from sklearn.linear_model import LogisticRegression

        p = np.clip(np.asarray(p, dtype="float64"), 1e-6, 1 - 1e-6)
        y = np.asarray(y, dtype="int64")
        logit = np.log(p / (1 - p)).reshape(-1, 1)
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=FutureWarning)
            self._model = LogisticRegression(penalty=None, solver="lbfgs", max_iter=1000)
            self._model.fit(logit, y)
        return self

    def transform(self, p: np.ndarray) -> np.ndarray:
        if self._model is None:
            raise RuntimeError("PlattCalibrator used before fit")
        p = np.clip(np.asarray(p, dtype="float64"), 1e-6, 1 - 1e-6)
        logit = np.log(p / (1 - p)).reshape(-1, 1)
        return self._check(self._model.predict_proba(logit)[:, 1])


class IsotonicCalibrator(Calibrator):
    """Free-form monotone recalibration.

    More flexible than Platt and correspondingly easier to overfit: with few
    samples it can produce flat steps that are artefacts rather than structure.
    Selection on validation data is what keeps that honest.
    """

    name = "isotonic"

    def __init__(self) -> None:
        self._model = None

    def fit(self, p, y) -> "IsotonicCalibrator":
        from sklearn.isotonic import IsotonicRegression

        self._model = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
        self._model.fit(np.asarray(p, dtype="float64"), np.asarray(y, dtype="float64"))
        return self

    def transform(self, p: np.ndarray) -> np.ndarray:
        if self._model is None:
            raise RuntimeError("IsotonicCalibrator used before fit")
        return self._check(self._model.predict(np.asarray(p, dtype="float64")))


def default_calibrators() -> list[Calibrator]:
    return [IdentityCalibrator(), PlattCalibrator(), IsotonicCalibrator()]


# ---------------------------------------------------------------------------

@dataclass
class CalibrationReport:
    label: str
    n: int
    brier: float
    logloss: float
    intercept: float
    slope: float
    ece: float
    mce: float
    buckets: list[BucketStat] = field(default_factory=list)

    def as_row(self) -> dict:
        return {
            "label": self.label, "n": self.n,
            "brier": self.brier, "logloss": self.logloss,
            "calibration_intercept": self.intercept,
            "calibration_slope": self.slope,
            "ece": self.ece, "mce": self.mce,
        }

    @property
    def verdict(self) -> str:
        """One-line human reading of the slope/intercept pair."""
        if not np.isfinite(self.slope):
            return "undetermined"
        if self.slope < 0.8:
            return "OVERCONFIDENT (probabilities too extreme)"
        if self.slope > 1.25:
            return "UNDERCONFIDENT (probabilities too timid)"
        if abs(self.intercept) > 0.25:
            return "BIASED (systematic offset)"
        return "reasonably calibrated"


def assess(p: np.ndarray, y: np.ndarray, label: str = "model") -> CalibrationReport:
    """Full calibration assessment of one probability vector."""
    p = np.asarray(p, dtype="float64")
    y = np.asarray(y, dtype="int64")
    intercept, slope = calibration_intercept_slope(p, y)
    buckets = reliability_table(p, y)
    ece, mce = expected_calibration_error(buckets)
    return CalibrationReport(
        label=label, n=len(p),
        brier=brier_score(p, y), logloss=log_loss(p, y),
        intercept=intercept, slope=slope, ece=ece, mce=mce, buckets=buckets,
    )


def select_calibrator(
    p_fit: np.ndarray,
    y_fit: np.ndarray,
    p_select: np.ndarray,
    y_select: np.ndarray,
    *,
    calibrators: Optional[Sequence[Calibrator]] = None,
    criterion: str = "logloss",
) -> tuple[Calibrator, dict[str, float]]:
    """Fit each calibrator on one set and choose using a DIFFERENT set.

    The two sets are separate arguments precisely so that passing the same rows
    to both is a visible act rather than an accident. Section 5 requires the
    choice be made on validation data only.
    """
    calibrators = list(calibrators) if calibrators is not None else default_calibrators()
    scores: dict[str, float] = {}
    best: Optional[Calibrator] = None
    best_score = float("inf")

    for calibrator in calibrators:
        try:
            calibrator.fit(p_fit, y_fit)
            transformed = calibrator.transform(p_select)
            score = (
                log_loss(transformed, y_select) if criterion == "logloss"
                else brier_score(transformed, y_select)
            )
        except Exception:  # noqa: BLE001 - a failed calibrator is simply not selected
            score = float("inf")
        scores[calibrator.name] = score
        if score < best_score:
            best_score = score
            best = calibrator

    if best is None:
        best = IdentityCalibrator()
    return best, scores
