"""Step 6 statistical/ML complementarity research (shadow only).

This module evaluates already-produced probabilities.  It does not fit either
production research model, create an ensemble, or import any live/UI/voice/
selection/forward component.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

MODEL_COMPLEMENTARITY_POLICY = "KALSHI_COMPLEMENTARITY_V1"
STATUS = "RESEARCH_SHADOW_ONLY"
OPENED_HOLDOUT_LABEL = "PREVIOUSLY_OPENED_HOLDOUT_NOT_STEP6_SEALED"
DISAGREEMENT_BUCKETS = (-np.inf, .01, .025, .05, .10, .20, np.inf)
DISAGREEMENT_LABELS = ("<0.01", "0.01-0.025", "0.025-0.05", "0.05-0.10", "0.10-0.20", ">=0.20")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def verify_frozen_artifacts(manifest_path: Path, model_path: Path, *, schema_hash: str,
                            data_hash: str | None = None, config_hash: str | None = None) -> dict:
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    checks = {
        "model_hash": sha256_file(model_path),
        "feature_schema_hash": schema_hash,
        "data_hash": data_hash or manifest["data_hash"],
        "config_hash": config_hash or manifest["config_hash"],
    }
    for name, actual in checks.items():
        if actual != manifest.get(name):
            raise ValueError(f"frozen Step 5 {name} mismatch")
    if manifest.get("version") != "KALSHI_PROBABILITY_RESEARCH_V1":
        raise ValueError("unexpected frozen Step 5 model identity")
    if manifest.get("frozen_candidate") != {"kind": "hist_gradient_boosting", "calibration": "platt"}:
        raise ValueError("unexpected frozen Step 5 candidate")
    return manifest


def build_comparison_states(table: pd.DataFrame, states: pd.DataFrame,
                            p_ml_yes, fixed_entry_mask) -> pd.DataFrame:
    """Align causal metadata and two frozen predictions, then attach labels for evaluation."""
    if len(table) != len(states) or not table.index.equals(states.index):
        raise ValueError("exact statistical/ML row alignment failed")
    required_table = {"contract_id", "asset", "group_key", "scan_utc", "window_start_utc",
                      "window_end_utc", "seconds_remaining", "kalshi_target", "spot",
                      "outcome_yes", "realized_vol_1m", "sigma_remaining_return", "crossings",
                      "reference_gap_bps"}
    required_states = {"p_yes", "normal_z"}
    if required_table - set(table) or required_states - set(states):
        raise ValueError("missing comparison-state columns")
    stat = states.p_yes.to_numpy(dtype=float)
    ml = np.asarray(p_ml_yes, dtype=float)
    y = table.outcome_yes.to_numpy(dtype=int)
    entry = np.asarray(fixed_entry_mask, dtype=bool)
    if not (len(ml) == len(y) == len(entry)) or not np.isfinite(np.c_[stat, ml]).all():
        raise ValueError("invalid or non-finite aligned probabilities")
    if np.any((stat < 0) | (stat > 1) | (ml < 0) | (ml > 1)) or not set(np.unique(y)) <= {0, 1}:
        raise ValueError("invalid probability or settlement label")
    out = table[["contract_id", "asset", "group_key", "scan_utc", "window_start_utc",
                 "window_end_utc", "seconds_remaining", "kalshi_target", "spot", "outcome_yes",
                 "realized_vol_1m", "sigma_remaining_return", "crossings", "reference_gap_bps"]].copy()
    out["row_id"] = [hashlib.sha256(f"{c}|{a}|{pd.Timestamp(t).isoformat()}".encode()).hexdigest()
                     for c, a, t in zip(out.contract_id, out.asset, out.scan_utc)]
    if out.row_id.duplicated().any():
        raise ValueError("duplicate comparison row identity")
    out["p_stat_yes"], out["p_ml_yes"] = stat, ml
    out["stat_pred_yes"], out["ml_pred_yes"] = stat >= .5, ml >= .5
    out["stat_selected_confidence"] = np.maximum(stat, 1 - stat)
    out["ml_selected_confidence"] = np.maximum(ml, 1 - ml)
    out["stat_probability_correct_side"] = np.where(y == 1, stat, 1 - stat)
    out["ml_probability_correct_side"] = np.where(y == 1, ml, 1 - ml)
    out["signed_disagreement"] = ml - stat
    out["abs_disagreement"] = np.abs(ml - stat)
    out["model_direction_agree"] = out.stat_pred_yes == out.ml_pred_yes
    out["stat_correct"] = out.stat_pred_yes.to_numpy() == y
    out["ml_correct"] = out.ml_pred_yes.to_numpy() == y
    out["both_correct"] = out.stat_correct & out.ml_correct
    out["stat_only_correct"] = out.stat_correct & ~out.ml_correct
    out["ml_only_correct"] = ~out.stat_correct & out.ml_correct
    out["both_wrong"] = ~out.stat_correct & ~out.ml_correct
    out["fixed_step4_entry"] = entry
    out["target_distance_z"] = states.normal_z.to_numpy(dtype=float)
    return out


def fit_development_regimes(frame: pd.DataFrame) -> dict:
    if frame.empty:
        raise ValueError("development data required for regimes")
    return {
        "volatility": [float(frame.realized_vol_1m.quantile(q)) for q in (.333333, .666667)],
        "reference_gap_abs_bps": [float(frame.reference_gap_bps.abs().quantile(q)) for q in (.333333, .666667)],
    }


def apply_regimes(frame: pd.DataFrame, thresholds: dict) -> pd.DataFrame:
    out = frame.copy()
    v0, v1 = thresholds["volatility"]
    g0, g1 = thresholds["reference_gap_abs_bps"]
    out["volatility_regime"] = pd.cut(out.realized_vol_1m, [-np.inf, v0, v1, np.inf],
                                      labels=["LOW", "MEDIUM", "HIGH"])
    out["reference_gap_regime"] = pd.cut(out.reference_gap_bps.abs(), [-np.inf, g0, g1, np.inf],
                                         labels=["LOW", "MEDIUM", "HIGH"])
    out["time_regime"] = pd.cut(out.seconds_remaining, [-np.inf, 60, 300, 600, np.inf],
                                labels=["FINAL_0_60", "LATE_60_300", "MIDDLE_300_600", "EARLY_GT_600"])
    out["target_distance_regime"] = pd.cut(out.target_distance_z.abs(), [-np.inf, .5, 1, 2, np.inf],
                                           labels=["LT_0.5Z", "0.5_1Z", "1_2Z", "GE_2Z"])
    out["crossing_regime"] = np.where(out.crossings == 0, "ZERO", np.where(out.crossings == 1, "ONE", "MULTIPLE"))
    out["disagreement_bucket"] = pd.cut(out.abs_disagreement, DISAGREEMENT_BUCKETS,
                                        labels=DISAGREEMENT_LABELS, right=False)
    return out


def _metrics(frame: pd.DataFrame) -> dict:
    if frame.empty:
        return {"n": 0}
    y = frame.outcome_yes.to_numpy(dtype=float)
    result = {"n": int(len(frame)), "share": None,
              "eventual_yes_rate": float(y.mean()),
              "stat_accuracy": float(frame.stat_correct.mean()), "ml_accuracy": float(frame.ml_correct.mean()),
              "both_correct_rate": float(frame.both_correct.mean()),
              "exactly_one_correct_rate": float((frame.stat_only_correct | frame.ml_only_correct).mean()),
              "both_wrong_rate": float(frame.both_wrong.mean()),
              "mean_stat_confidence": float(frame.stat_selected_confidence.mean()),
              "mean_ml_confidence": float(frame.ml_selected_confidence.mean())}
    for prefix, p in (("stat", frame.p_stat_yes.to_numpy()), ("ml", frame.p_ml_yes.to_numpy())):
        p = np.clip(p, 1e-12, 1 - 1e-12)
        result[f"{prefix}_brier"] = float(np.mean((p - y) ** 2))
        result[f"{prefix}_log_loss"] = float(-np.mean(y*np.log(p) + (1-y)*np.log(1-p)))
        result[f"{prefix}_failure_rate"] = float(1 - frame[f"{prefix}_correct"].mean())
    return result


def grouped_metrics(frame: pd.DataFrame, column: str, values=None) -> dict:
    values = list(values) if values is not None else [x for x in frame[column].dropna().unique()]
    total = len(frame)
    result = {}
    for value in values:
        row = _metrics(frame[frame[column].astype(str) == str(value)])
        row["share"] = float(row["n"] / total) if total else None
        result[str(value)] = row
    return result


def direction_analysis(frame: pd.DataFrame) -> dict:
    groups = np.select([
        frame.stat_pred_yes & frame.ml_pred_yes,
        ~frame.stat_pred_yes & ~frame.ml_pred_yes,
        frame.stat_pred_yes & ~frame.ml_pred_yes,
    ], ["A1_BOTH_YES", "A2_BOTH_NO", "A3_STAT_YES_ML_NO"], default="A4_STAT_NO_ML_YES")
    work = frame.assign(direction_group=groups)
    rows = grouped_metrics(work, "direction_group",
                           ["A1_BOTH_YES", "A2_BOTH_NO", "A3_STAT_YES_ML_NO", "A4_STAT_NO_ML_YES"])
    for name in rows:
        subset = work[work.direction_group == name]
        rows[name]["by_asset"] = grouped_metrics(subset, "asset")
        rows[name]["by_time"] = grouped_metrics(subset, "time_regime")
    return rows


def high_confidence(frame: pd.DataFrame) -> dict:
    result = {}
    for threshold in (.90, .95, .975, .99):
        subset = frame[frame.model_direction_agree &
                       (frame.stat_selected_confidence >= threshold) &
                       (frame.ml_selected_confidence >= threshold)]
        row = _metrics(subset); row["coverage"] = float(len(subset)/len(frame)) if len(frame) else None
        row["yes_no"] = grouped_metrics(subset.assign(predicted_side=np.where(subset.stat_pred_yes, "YES", "NO")), "predicted_side")
        row["assets"] = grouped_metrics(subset, "asset")
        result[str(threshold)] = row
    return result


def skepticism(frame: pd.DataFrame) -> dict:
    definitions = {
        "D1_STAT_GE_095_ML_LT_090": (frame.stat_selected_confidence >= .95) & (frame.ml_selected_confidence < .90),
        "D2_STAT_GE_0975_ML_LT_095": (frame.stat_selected_confidence >= .975) & (frame.ml_selected_confidence < .95),
        "D3_STAT_GE_099_ML_LT_095": (frame.stat_selected_confidence >= .99) & (frame.ml_selected_confidence < .95),
        "E1_ML_GE_095_STAT_LT_090": (frame.ml_selected_confidence >= .95) & (frame.stat_selected_confidence < .90),
        "E2_ML_GE_0975_STAT_LT_095": (frame.ml_selected_confidence >= .975) & (frame.stat_selected_confidence < .95),
    }
    return {name: _metrics(frame[mask]) for name, mask in definitions.items()}


def calibration_table(frame: pd.DataFrame) -> dict:
    bands = (.5, .6, .7, .8, .9, .95, .975, 1.000001)
    result = {}
    for model in ("stat", "ml"):
        confidence = frame[f"{model}_selected_confidence"]
        correct = frame[f"{model}_correct"]
        result[model] = [{"band": f"{lo:.3f}-{min(hi,1):.3f}", "n": int(mask.sum()),
                          "mean_confidence": float(confidence[mask].mean()),
                          "empirical_accuracy": float(correct[mask].mean())}
                         for lo, hi in zip(bands[:-1], bands[1:])
                         if (mask := (confidence >= lo) & (confidence < hi)).any()]
    return result


def conditional_calibration(frame: pd.DataFrame) -> dict:
    masks = {
        "AGREE": frame.model_direction_agree,
        "DISAGREE": ~frame.model_direction_agree,
        "HIGH_CONFIDENCE_AGREE": frame.model_direction_agree & (frame.stat_selected_confidence >= .95) & (frame.ml_selected_confidence >= .95),
        "STAT_CONFIDENT_ML_SKEPTICAL": (frame.stat_selected_confidence >= .95) & (frame.ml_selected_confidence < .90),
        "ML_CONFIDENT_STAT_SKEPTICAL": (frame.ml_selected_confidence >= .95) & (frame.stat_selected_confidence < .90),
    }
    return {name: {"n": int(mask.sum()), "calibration": calibration_table(frame[mask])}
            for name, mask in masks.items()}


def error_complementarity(frame: pd.DataFrame) -> dict:
    stat_error = (~frame.stat_correct).astype(float)
    ml_error = (~frame.ml_correct).astype(float)
    correlation = float(np.corrcoef(stat_error, ml_error)[0, 1]) if stat_error.std() and ml_error.std() else None
    return {"n": int(len(frame)), "error_correlation": correlation,
            "both_correct": float(frame.both_correct.mean()), "both_wrong": float(frame.both_wrong.mean()),
            "stat_only_correct": float(frame.stat_only_correct.mean()), "ml_only_correct": float(frame.ml_only_correct.mean())}


def cluster_bootstrap_rate(frame: pd.DataFrame, value: str, *, iterations=500, seed=20260815) -> dict:
    """Window-cluster bootstrap; state rows are never treated as independent."""
    if frame.empty:
        return {"estimate": None, "cluster_ci95": None, "clusters": 0}
    grouped = frame.groupby("group_key", sort=False)[value].agg(["sum", "count"]).to_numpy(float)
    rng = np.random.default_rng(seed); estimates = np.empty(iterations)
    for i in range(iterations):
        sample = grouped[rng.integers(0, len(grouped), len(grouped))]
        estimates[i] = sample[:, 0].sum() / sample[:, 1].sum()
    return {"estimate": float(frame[value].mean()),
            "cluster_ci95": [float(x) for x in np.quantile(estimates, [.025, .975])],
            "clusters": int(len(grouped))}


def incremental_information(development: pd.DataFrame, evaluation: pd.DataFrame) -> dict:
    """Fit diagnostic failure models on development only and score another split."""
    y_dev = (~development.stat_correct).astype(int).to_numpy()
    y_eval = (~evaluation.stat_correct).astype(int).to_numpy()
    base_names = ["stat_selected_confidence"]
    extended_names = ["stat_selected_confidence", "ml_selected_confidence", "signed_disagreement", "abs_disagreement"]
    result = {}
    for name, columns in (("stat_confidence_only", base_names), ("plus_ml_disagreement", extended_names)):
        model = LogisticRegression(C=1.0, max_iter=1000, random_state=20260815).fit(development[columns], y_dev)
        p = np.clip(model.predict_proba(evaluation[columns])[:, 1], 1e-12, 1-1e-12)
        result[name] = {"brier": float(np.mean((p-y_eval)**2)),
                        "log_loss": float(-np.mean(y_eval*np.log(p)+(1-y_eval)*np.log(1-p))),
                        "coefficients": {column: float(value) for column, value in zip(columns, model.coef_[0])}}
    result["delta_extended_minus_base"] = {
        metric: result["plus_ml_disagreement"][metric] - result["stat_confidence_only"][metric]
        for metric in ("brier", "log_loss")}
    return result


def analyze_split(frame: pd.DataFrame) -> dict:
    disagree = frame[~frame.model_direction_agree]
    return {
        "overall": _metrics(frame), "direction": direction_analysis(frame),
        "probability_disagreement": grouped_metrics(frame, "disagreement_bucket", DISAGREEMENT_LABELS),
        "high_confidence_agreement": high_confidence(frame), "skepticism": skepticism(frame),
        "error_complementarity": error_complementarity(frame),
        "clustered_direction_disagreement": {
            "stat_accuracy": cluster_bootstrap_rate(disagree, "stat_correct"),
            "ml_accuracy": cluster_bootstrap_rate(disagree, "ml_correct")},
        "direction_disagreement_rescue": {
            "overall": _metrics(disagree), "assets": grouped_metrics(disagree, "asset"),
            "sides": grouped_metrics(disagree.assign(stat_side=np.where(disagree.stat_pred_yes, "STAT_YES", "STAT_NO")), "stat_side"),
            "time": grouped_metrics(disagree, "time_regime"),
            "volatility": grouped_metrics(disagree, "volatility_regime"),
            "target_distance": grouped_metrics(disagree, "target_distance_regime"),
            "crossings": grouped_metrics(disagree, "crossing_regime"),
            "reference_gap": grouped_metrics(disagree, "reference_gap_regime")},
        "regimes": {"assets": grouped_metrics(frame, "asset"), "time": grouped_metrics(frame, "time_regime"),
                    "volatility": grouped_metrics(frame, "volatility_regime"),
                    "target_distance": grouped_metrics(frame, "target_distance_regime"),
                    "crossings": grouped_metrics(frame, "crossing_regime"),
                    "reference_gap": grouped_metrics(frame, "reference_gap_regime")},
        "conditional_calibration": conditional_calibration(frame),
        "clustered_accuracy": {"stat": cluster_bootstrap_rate(frame, "stat_correct"),
                               "ml": cluster_bootstrap_rate(frame, "ml_correct")},
    }
