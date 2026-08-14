"""
MANTIS V4 — window-aware evaluation (Phase 4 sections 6, 7, 8).

CLUSTERED UNCERTAINTY IS NOT OPTIONAL HERE

Phase 3 measured 0.641 mean pairwise return correlation across the five assets
and only 1.72 effective independent assets of five. Phase 4 adds a second layer:
16 scan points per contract share ONE label, so they are close to 16 copies of
one observation.

An ordinary Wilson or bootstrap interval over rows assumes those rows are
independent. They are not, by a wide margin, so such an interval is badly too
narrow — it would report a precision the data cannot support.

Every interval in this module is therefore computed by resampling WINDOWS, not
rows. A window is drawn with replacement and all of its rows come with it, so
both correlation layers are preserved in the resample. That is the block
bootstrap, and it is the honest way to put error bars on this dataset.

The difference is not cosmetic. On this data the clustered interval is several
times wider than the naive one, and reporting the naive number would be the
single most misleading thing Phase 4 could do.

SELECTIVE PREDICTION (section 6)

"How accurate can MANTIS become if it becomes increasingly selective?" is
answered by sweeping a confidence threshold and reporting coverage against
accuracy. The honest framing is that coverage FALLS as accuracy rises, and that
the accuracy gain must be weighed against contract price — which Phase 4 still
cannot see.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np

from .calibration import brier_score, log_loss, wilson

DEFAULT_SEED = 20260813
DEFAULT_BOOTSTRAP = 2000

# Phase 4 section 6 names these thresholds explicitly.
DECISION_THRESHOLDS = (0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95)

# Phase 4 section 7 names these entry-time buckets explicitly.
ENTRY_TIME_BUCKETS = (
    ("600-900s", 600, 901),
    ("450-600s", 450, 600),
    ("300-450s", 300, 450),
    ("150-300s", 150, 300),
    ("60-150s", 60, 150),
    ("30-60s", 30, 60),
    ("0-30s", 0, 30),
)


def _as_arrays(p, y, groups):
    return (
        np.asarray(p, dtype="float64"),
        np.asarray(y, dtype="int64"),
        np.asarray(groups),
    )


@dataclass
class ClusteredInterval:
    point: float
    low: float
    high: float
    n_rows: int
    n_groups: int
    naive_low: float = float("nan")
    naive_high: float = float("nan")

    @property
    def width(self) -> float:
        return self.high - self.low

    @property
    def naive_width(self) -> float:
        return self.naive_high - self.naive_low

    @property
    def inflation(self) -> float:
        """How much wider the honest interval is than the naive one."""
        if not np.isfinite(self.naive_width) or self.naive_width <= 0:
            return float("nan")
        return self.width / self.naive_width


def clustered_bootstrap(
    values: np.ndarray,
    groups: np.ndarray,
    statistic,
    *,
    n_boot: int = DEFAULT_BOOTSTRAP,
    seed: int = DEFAULT_SEED,
    alpha: float = 0.05,
) -> tuple[float, float, float]:
    """Block bootstrap over groups. Returns (point, low, high).

    Windows are drawn with replacement; all rows of a drawn window are included.
    """
    rng = np.random.default_rng(seed)
    unique = np.unique(groups)
    if len(unique) < 2:
        point = statistic(values)
        return point, float("nan"), float("nan")

    index_by_group = {g: np.flatnonzero(groups == g) for g in unique}
    point = statistic(values)

    samples = np.empty(n_boot, dtype="float64")
    for i in range(n_boot):
        drawn = rng.choice(unique, size=len(unique), replace=True)
        idx = np.concatenate([index_by_group[g] for g in drawn])
        try:
            samples[i] = statistic(values[idx])
        except (ValueError, ZeroDivisionError):
            samples[i] = np.nan

    samples = samples[np.isfinite(samples)]
    if len(samples) < 10:
        return point, float("nan"), float("nan")
    return (
        point,
        float(np.percentile(samples, 100 * alpha / 2)),
        float(np.percentile(samples, 100 * (1 - alpha / 2))),
    )


def clustered_accuracy(
    correct: np.ndarray,
    groups: np.ndarray,
    *,
    n_boot: int = DEFAULT_BOOTSTRAP,
    seed: int = DEFAULT_SEED,
) -> ClusteredInterval:
    """Accuracy with a window-clustered interval, plus the naive one for contrast."""
    correct = np.asarray(correct, dtype="float64")
    groups = np.asarray(groups)
    if len(correct) == 0:
        return ClusteredInterval(float("nan"), float("nan"), float("nan"), 0, 0)

    point, low, high = clustered_bootstrap(
        correct, groups, lambda v: float(v.mean()), n_boot=n_boot, seed=seed
    )
    naive_low, naive_high = wilson(int(correct.sum()), len(correct))
    return ClusteredInterval(
        point=point, low=low, high=high,
        n_rows=len(correct), n_groups=int(len(np.unique(groups))),
        naive_low=naive_low, naive_high=naive_high,
    )


def clustered_metric(
    p: np.ndarray,
    y: np.ndarray,
    groups: np.ndarray,
    metric: str = "brier",
    *,
    n_boot: int = 500,
    seed: int = DEFAULT_SEED,
) -> ClusteredInterval:
    """Brier or log loss with a window-clustered interval."""
    p, y, groups = _as_arrays(p, y, groups)
    stacked = np.column_stack([p, y])

    def statistic(rows: np.ndarray) -> float:
        return (
            brier_score(rows[:, 0], rows[:, 1]) if metric == "brier"
            else log_loss(rows[:, 0], rows[:, 1])
        )

    point, low, high = clustered_bootstrap(
        stacked, groups, statistic, n_boot=n_boot, seed=seed
    )
    return ClusteredInterval(point, low, high, len(p), int(len(np.unique(groups))))


# ---------------------------------------------------------------------------
# Selective prediction
# ---------------------------------------------------------------------------

@dataclass
class SelectiveRow:
    threshold: float
    coverage: float
    n_trades: int
    n_groups: int
    accuracy: float
    ci_low: float
    ci_high: float
    naive_ci_low: float
    naive_ci_high: float
    brier: Optional[float]
    logloss: Optional[float]
    yes_trades: int
    yes_accuracy: Optional[float]
    no_trades: int
    no_accuracy: Optional[float]

    def as_row(self) -> dict:
        return {
            "threshold": self.threshold,
            "coverage": self.coverage,
            "n_trades": self.n_trades,
            "n_groups": self.n_groups,
            "accuracy": self.accuracy,
            "ci_low": self.ci_low,
            "ci_high": self.ci_high,
            "naive_ci_low": self.naive_ci_low,
            "naive_ci_high": self.naive_ci_high,
            "brier": self.brier,
            "logloss": self.logloss,
            "yes_trades": self.yes_trades,
            "yes_accuracy": self.yes_accuracy,
            "no_trades": self.no_trades,
            "no_accuracy": self.no_accuracy,
            "economic_profitability": "NOT EVALUABLE - no historical contract prices",
        }


def selective_prediction(
    p: np.ndarray,
    y: np.ndarray,
    groups: np.ndarray,
    *,
    thresholds: Sequence[float] = DECISION_THRESHOLDS,
    n_boot: int = 600,
    seed: int = DEFAULT_SEED,
) -> list[SelectiveRow]:
    """Coverage/accuracy curve as the confidence threshold rises.

    At each threshold the system takes the side it favours only when its
    confidence, ``max(p, 1-p)``, clears the bar. Everything else is a NO TRADE
    and is not scored — an abstention is neither correct nor incorrect.
    """
    p, y, groups = _as_arrays(p, y, groups)
    total = len(p)
    confidence = np.maximum(p, 1.0 - p)
    take_yes = p >= 0.5
    won = np.where(take_yes, y == 1, y == 0).astype("float64")

    rows: list[SelectiveRow] = []
    for threshold in thresholds:
        mask = confidence >= threshold
        n = int(mask.sum())
        if n == 0:
            rows.append(SelectiveRow(
                threshold, 0.0, 0, 0, float("nan"), float("nan"), float("nan"),
                float("nan"), float("nan"), None, None, 0, None, 0, None,
            ))
            continue

        interval = clustered_accuracy(won[mask], groups[mask], n_boot=n_boot, seed=seed)
        yes_mask = mask & take_yes
        no_mask = mask & ~take_yes

        rows.append(SelectiveRow(
            threshold=threshold,
            coverage=n / total,
            n_trades=n,
            n_groups=int(len(np.unique(groups[mask]))),
            accuracy=interval.point,
            ci_low=interval.low,
            ci_high=interval.high,
            naive_ci_low=interval.naive_low,
            naive_ci_high=interval.naive_high,
            brier=brier_score(p[mask], y[mask]),
            logloss=log_loss(p[mask], y[mask]),
            yes_trades=int(yes_mask.sum()),
            yes_accuracy=float(won[yes_mask].mean()) if yes_mask.any() else None,
            no_trades=int(no_mask.sum()),
            no_accuracy=float(won[no_mask].mean()) if no_mask.any() else None,
        ))
    return rows


# ---------------------------------------------------------------------------
# Slicing helpers
# ---------------------------------------------------------------------------

def bucket_by_entry_time(seconds_remaining: np.ndarray) -> np.ndarray:
    """Label each row with its Phase 4 section 7 entry-time bucket."""
    seconds = np.asarray(seconds_remaining, dtype="float64")
    labels = np.full(len(seconds), "other", dtype=object)
    for name, low, high in ENTRY_TIME_BUCKETS:
        labels[(seconds >= low) & (seconds < high)] = name
    return labels


@dataclass
class SliceResult:
    label: str
    n: int
    n_groups: int
    accuracy: float
    ci_low: float
    ci_high: float
    brier: float
    logloss: float
    base_rate_yes: float
    mean_abs_z: float = float("nan")

    def as_row(self) -> dict:
        return {
            "label": self.label, "n": self.n, "n_groups": self.n_groups,
            "accuracy": self.accuracy, "ci_low": self.ci_low, "ci_high": self.ci_high,
            "brier": self.brier, "logloss": self.logloss,
            "base_rate_yes": self.base_rate_yes,
            "mean_abs_z": self.mean_abs_z,
        }


def evaluate_slice(
    p: np.ndarray,
    y: np.ndarray,
    groups: np.ndarray,
    label: str,
    *,
    abs_z: Optional[np.ndarray] = None,
    n_boot: int = 400,
    seed: int = DEFAULT_SEED,
) -> SliceResult:
    p, y, groups = _as_arrays(p, y, groups)
    if len(p) == 0:
        return SliceResult(label, 0, 0, float("nan"), float("nan"), float("nan"),
                           float("nan"), float("nan"), float("nan"))

    take_yes = p >= 0.5
    won = np.where(take_yes, y == 1, y == 0).astype("float64")
    interval = clustered_accuracy(won, groups, n_boot=n_boot, seed=seed)

    return SliceResult(
        label=label, n=len(p), n_groups=int(len(np.unique(groups))),
        accuracy=interval.point, ci_low=interval.low, ci_high=interval.high,
        brier=brier_score(p, y), logloss=log_loss(p, y),
        base_rate_yes=float(y.mean()),
        mean_abs_z=float(np.nanmean(np.abs(abs_z))) if abs_z is not None else float("nan"),
    )


def paired_bootstrap_difference(
    p_a: np.ndarray,
    p_b: np.ndarray,
    y: np.ndarray,
    groups: np.ndarray,
    metric: str = "brier",
    *,
    n_boot: int = 1000,
    seed: int = DEFAULT_SEED,
) -> tuple[float, float, float]:
    """Clustered paired bootstrap of metric(A) - metric(B) on identical rows.

    Paired and clustered: the same windows are drawn for both models, so the
    comparison is not contaminated by which windows happened to be sampled.
    For Brier and log loss a NEGATIVE difference means A is better.
    """
    p_a = np.asarray(p_a, dtype="float64")
    p_b = np.asarray(p_b, dtype="float64")
    y = np.asarray(y, dtype="int64")
    groups = np.asarray(groups)

    stacked = np.column_stack([p_a, p_b, y])

    def statistic(rows: np.ndarray) -> float:
        if metric == "brier":
            return brier_score(rows[:, 0], rows[:, 2]) - brier_score(rows[:, 1], rows[:, 2])
        if metric == "logloss":
            return log_loss(rows[:, 0], rows[:, 2]) - log_loss(rows[:, 1], rows[:, 2])
        a = np.where(rows[:, 0] >= 0.5, rows[:, 2] == 1, rows[:, 2] == 0).mean()
        b = np.where(rows[:, 1] >= 0.5, rows[:, 2] == 1, rows[:, 2] == 0).mean()
        return float(a - b)

    return clustered_bootstrap(stacked, groups, statistic, n_boot=n_boot, seed=seed)
