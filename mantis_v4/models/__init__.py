"""
MANTIS V4 — Phase 4 transparent probability models.

Phase 4 answers exactly one question:

    "When MANTIS says an outcome has probability p, how close is that number to
     the observed frequency out of sample?"

Calibration matters more than raw accuracy here, because every downstream EV
calculation consumes the probability directly. A miscalibrated 90% produces a
confidently wrong expected value.

Reading order:
    dataset_builder.py     scan-grid feature/label table, window-grouped
    features_extended.py   the extra causal feature families
    splits.py              grouped walk-forward + SEALED holdout
    estimators.py          Normal-Z, logistic, regularized logistic, probit
    calibration.py         Platt / isotonic / identity + reliability metrics
    evaluation.py          clustered bootstrap, selective prediction
    pipeline.py            fold orchestration

Standing limitations, unchanged from Phase 3:
    PROXY SETTLEMENT REFERENCE
    NO HISTORICAL WEBULL CONTRACT QUOTES
    CLASSIFICATION RESEARCH ONLY - NOT A PROFITABILITY BACKTEST
    LIMITED RECENT HISTORICAL REGIME
"""

from .calibration import (
    CONFIDENCE_BUCKETS,
    CalibrationReport,
    Calibrator,
    IdentityCalibrator,
    IsotonicCalibrator,
    PlattCalibrator,
    assess,
    brier_score,
    default_calibrators,
    expected_calibration_error,
    log_loss,
    reliability_table,
    select_calibrator,
)
from .dataset_builder import (
    ALL_FEATURES,
    DEFAULT_SCAN_GRID,
    FEATURE_FAMILIES,
    ModellingDatasetBuilder,
    drop_incomplete,
    select_usable_features,
)
from .estimators import (
    LogisticModel,
    NormalZModel,
    ProbabilityModel,
    ProbitModel,
    StandardScaler,
    default_models,
    load_model,
    save_coefficients,
    save_model,
)
from .evaluation import (
    DECISION_THRESHOLDS,
    ENTRY_TIME_BUCKETS,
    ClusteredInterval,
    bucket_by_entry_time,
    clustered_accuracy,
    clustered_metric,
    evaluate_slice,
    paired_bootstrap_difference,
    selective_prediction,
)
from .features_extended import build_cross_asset_panel, precompute_extended
from .pipeline import WalkForwardResult, fit_final_model, run_walk_forward
from .splits import (
    GroupedSplit,
    HoldoutSeal,
    SplitPlan,
    assert_split_integrity,
    grouped_walk_forward,
    reserve_holdout,
    summarise_splits,
)

__all__ = [
    "ALL_FEATURES", "CONFIDENCE_BUCKETS", "CalibrationReport", "Calibrator",
    "ClusteredInterval", "DECISION_THRESHOLDS", "DEFAULT_SCAN_GRID",
    "ENTRY_TIME_BUCKETS", "FEATURE_FAMILIES", "GroupedSplit", "HoldoutSeal",
    "IdentityCalibrator", "IsotonicCalibrator", "LogisticModel",
    "ModellingDatasetBuilder", "NormalZModel", "PlattCalibrator",
    "ProbabilityModel", "ProbitModel", "SplitPlan", "StandardScaler",
    "WalkForwardResult", "assert_split_integrity", "assess",
    "brier_score", "bucket_by_entry_time", "build_cross_asset_panel",
    "clustered_accuracy", "clustered_metric", "default_calibrators",
    "default_models", "drop_incomplete", "evaluate_slice",
    "expected_calibration_error", "fit_final_model", "grouped_walk_forward",
    "load_model", "log_loss", "paired_bootstrap_difference",
    "precompute_extended", "reliability_table", "reserve_holdout",
    "run_walk_forward", "save_coefficients", "save_model",
    "select_usable_features",
    "select_calibrator", "selective_prediction", "summarise_splits",
]
