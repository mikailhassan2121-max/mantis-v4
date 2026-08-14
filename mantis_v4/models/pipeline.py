"""
MANTIS V4 — Phase 4 fitting pipeline.

Runs a model through window-grouped walk-forward folds and collects
OUT-OF-SAMPLE predictions only. Nothing here ever reports an in-sample number,
because an in-sample number on a fitted model answers no question anyone has.

THE CALIBRATION SPLIT INSIDE EACH FOLD

Section 5 requires that the calibrator be fitted and chosen without touching
the data it is scored on. Inside every training block the rows are therefore
split CHRONOLOGICALLY and BY WINDOW again:

    train block ->  [ model-fit portion | calibration portion ]
                                          (the last 25% of windows)

The model is fitted on the first portion, the calibrator on the second, and
both are applied to the fold's test block which neither has seen. A calibrator
fitted on the same rows the model was fitted on would be calibrating against
the model's memorised training predictions, which are systematically sharper
than its out-of-sample ones — the calibration would then be corrected in the
wrong direction.

DETERMINISM

Every model takes an explicit seed and every bootstrap takes an explicit seed.
``test_deterministic_under_fixed_seed`` runs a full fold twice and requires
bit-identical predictions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np
import pandas as pd

from .calibration import (
    Calibrator,
    IdentityCalibrator,
    assess,
    default_calibrators,
    select_calibrator,
)
from .estimators import DEFAULT_SEED, ProbabilityModel
from .splits import GroupedSplit, assert_split_integrity, grouped_walk_forward


@dataclass
class FoldResult:
    fold: int
    test_idx: np.ndarray
    p_raw: np.ndarray
    p_calibrated: np.ndarray
    y: np.ndarray
    groups: np.ndarray
    calibrator_name: str
    calibrator_scores: dict[str, float] = field(default_factory=dict)
    train_rows: int = 0
    train_groups: int = 0
    converged: Optional[bool] = None


@dataclass
class WalkForwardResult:
    """Pooled out-of-sample predictions across every fold."""

    model_name: str
    feature_set: str
    folds: list[FoldResult] = field(default_factory=list)

    def _concat(self, attribute: str) -> np.ndarray:
        if not self.folds:
            return np.array([])
        return np.concatenate([getattr(f, attribute) for f in self.folds])

    @property
    def p_raw(self) -> np.ndarray:
        return self._concat("p_raw")

    @property
    def p_calibrated(self) -> np.ndarray:
        return self._concat("p_calibrated")

    @property
    def y(self) -> np.ndarray:
        return self._concat("y")

    @property
    def groups(self) -> np.ndarray:
        return self._concat("groups")

    @property
    def test_idx(self) -> np.ndarray:
        return self._concat("test_idx")

    @property
    def n(self) -> int:
        return len(self.y)

    @property
    def calibrator_votes(self) -> dict[str, int]:
        """Which calibrator each fold chose. Disagreement is informative."""
        votes: dict[str, int] = {}
        for fold in self.folds:
            votes[fold.calibrator_name] = votes.get(fold.calibrator_name, 0) + 1
        return votes

    @property
    def majority_calibrator(self) -> str:
        votes = self.calibrator_votes
        return max(votes, key=votes.get) if votes else "uncalibrated"


def split_train_for_calibration(
    table: pd.DataFrame,
    train_idx: np.ndarray,
    *,
    calibration_fraction: float = 0.25,
    group_column: str = "group_key",
    time_column: str = "window_epoch",
) -> tuple[np.ndarray, np.ndarray]:
    """Chronologically split a training block into (model-fit, calibration).

    Grouped by window, so no contract straddles the internal boundary.
    """
    block = table.iloc[train_idx]
    order = (
        block[[group_column, time_column]]
        .drop_duplicates(subset=[group_column])
        .sort_values(time_column)
    )
    groups = order[group_column].tolist()
    if len(groups) < 4:
        return train_idx, np.array([], dtype=int)

    n_cal = max(1, int(round(len(groups) * calibration_fraction)))
    calibration_groups = set(groups[-n_cal:])

    is_calibration = block[group_column].isin(calibration_groups).to_numpy()
    return train_idx[~is_calibration], train_idx[is_calibration]


def run_walk_forward(
    table: pd.DataFrame,
    features: Sequence[str],
    model_factory,
    *,
    splits: Optional[Sequence[GroupedSplit]] = None,
    folds: int = 5,
    label_column: str = "outcome_yes",
    group_column: str = "group_key",
    calibration_fraction: float = 0.25,
    calibrators: Optional[Sequence[Calibrator]] = None,
    model_name: Optional[str] = None,
    feature_set: str = "custom",
    verify: bool = True,
) -> WalkForwardResult:
    """Fit and evaluate one model across window-grouped chronological folds.

    ``model_factory`` is a zero-argument callable returning a FRESH model, so
    no state can survive from one fold into the next.
    """
    features = list(features)
    X_all = table[features].to_numpy(dtype="float64")
    y_all = table[label_column].to_numpy(dtype="int64")
    groups_all = table[group_column].to_numpy()

    # Fail with a message that names the culprit. Passing a feature that was
    # removed during cleaning (or one that is structurally missing) otherwise
    # surfaces as an opaque sklearn "Input X contains NaN" several frames deep.
    finite = np.isfinite(X_all)
    if not finite.all():
        offenders = {
            features[i]: float(1.0 - finite[:, i].mean())
            for i in range(len(features))
            if not finite[:, i].all()
        }
        raise ValueError(
            "Non-finite values in the feature matrix. Offending features "
            "(name -> fraction missing): "
            + ", ".join(f"{k} -> {v:.3f}" for k, v in sorted(offenders.items()))
            + ". Use select_usable_features() to drop structurally-missing "
              "features BEFORE dropping rows."
        )

    if splits is None:
        splits = grouped_walk_forward(table, folds=folds)

    probe = model_factory()
    result = WalkForwardResult(
        model_name=model_name or probe.name, feature_set=feature_set
    )

    for split in splits:
        if verify:
            assert_split_integrity(table, split)

        if probe.requires_fit:
            fit_idx, cal_idx = split_train_for_calibration(
                table, split.train_idx, calibration_fraction=calibration_fraction
            )
        else:
            # A model with no parameters needs no fitting portion; the whole
            # training block can serve calibration.
            fit_idx, cal_idx = split.train_idx, split.train_idx

        if len(fit_idx) == 0:
            continue

        model = model_factory()
        model.fit(X_all[fit_idx], y_all[fit_idx], features)

        p_test_raw = model.predict_proba(X_all[split.test_idx])

        calibrator: Calibrator = IdentityCalibrator()
        scores: dict[str, float] = {}
        if len(cal_idx) >= 50:
            p_cal = model.predict_proba(X_all[cal_idx])
            # Fit and select on the calibration block only. The test block is
            # never touched by either step.
            half = len(cal_idx) // 2
            calibrator, scores = select_calibrator(
                p_cal[:half], y_all[cal_idx][:half],
                p_cal[half:], y_all[cal_idx][half:],
                calibrators=list(calibrators) if calibrators else default_calibrators(),
            )
            # Refit the winner on the full calibration block for maximum data.
            calibrator.fit(p_cal, y_all[cal_idx])

        p_test_cal = calibrator.transform(p_test_raw)

        result.folds.append(
            FoldResult(
                fold=split.fold,
                test_idx=split.test_idx,
                p_raw=p_test_raw,
                p_calibrated=p_test_cal,
                y=y_all[split.test_idx],
                groups=groups_all[split.test_idx],
                calibrator_name=calibrator.name,
                calibrator_scores=scores,
                train_rows=len(fit_idx),
                train_groups=split.train_groups,
                converged=getattr(model, "converged_", None),
            )
        )

    return result


def fit_final_model(
    table: pd.DataFrame,
    features: Sequence[str],
    model_factory,
    *,
    calibration_fraction: float = 0.25,
    calibrators: Optional[Sequence[Calibrator]] = None,
) -> tuple[ProbabilityModel, Calibrator, dict]:
    """Fit one model + calibrator on the ENTIRE development set.

    This is the artefact evaluated once against the sealed holdout. The
    internal fit/calibration split is chronological and window-grouped, exactly
    as inside a fold.
    """
    features = list(features)
    X = table[features].to_numpy(dtype="float64")
    y = table["outcome_yes"].to_numpy(dtype="int64")
    all_idx = np.arange(len(table))

    probe = model_factory()
    if probe.requires_fit:
        fit_idx, cal_idx = split_train_for_calibration(
            table, all_idx, calibration_fraction=calibration_fraction
        )
    else:
        fit_idx, cal_idx = all_idx, all_idx

    model = model_factory()
    model.fit(X[fit_idx], y[fit_idx], features)

    calibrator: Calibrator = IdentityCalibrator()
    scores: dict[str, float] = {}
    if len(cal_idx) >= 50:
        p_cal = model.predict_proba(X[cal_idx])
        half = len(cal_idx) // 2
        calibrator, scores = select_calibrator(
            p_cal[:half], y[cal_idx][:half],
            p_cal[half:], y[cal_idx][half:],
            calibrators=list(calibrators) if calibrators else default_calibrators(),
        )
        calibrator.fit(p_cal, y[cal_idx])

    meta = {
        "fit_rows": int(len(fit_idx)),
        "calibration_rows": int(len(cal_idx)),
        "calibrator": calibrator.name,
        "calibrator_scores": scores,
        "converged": getattr(model, "converged_", None),
    }
    return model, calibrator, meta
