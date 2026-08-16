"""Leakage-resistant Step 5 probability baselines for Kalshi research.

This module has no dependency on live selection, UI, voice, forward storage, or
economics.  Outcomes are accepted only as a separate target vector; the feature
builder uses a fixed causal allow-list and rejects non-finite rows.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

STEP5_MODEL_FAMILY = "KALSHI_PROBABILITY_RESEARCH_V1"
SEED = 20260815

NUMERIC_FEATURES = (
    "kalshi_buffer_pct", "kalshi_normal_z", "log_seconds_remaining",
    "realized_vol_1m", "rv_5m", "rv_15m", "rv_30m",
    "mom1", "mom3", "mom5", "mom10", "momentum_acceleration",
    "vol_ratio_short_long", "crossings", "reference_gap_bps",
    "elapsed_fraction", "hour_of_day_sin", "hour_of_day_cos", "is_weekend",
)
ASSET_FEATURES = ("asset_BTC", "asset_ETH", "asset_SOL", "asset_XRP")
FEATURE_NAMES = NUMERIC_FEATURES + ASSET_FEATURES

FORBIDDEN_FEATURE_TOKENS = (
    "outcome", "result", "settlement", "terminal", "expiration_value",
    "ending_benchmark", "future", "correct", "label",
)

FEATURE_CAUSALITY = {
    "kalshi_buffer_pct": "current causal close minus known Kalshi target, divided by target",
    "kalshi_normal_z": "target-relative buffer divided by causal remaining volatility",
    "log_seconds_remaining": "exchange window clock known at scan timestamp",
    "realized_vol_1m": "returns ending at or before scan timestamp",
    "rv_5m": "five-minute causal return window",
    "rv_15m": "fifteen-minute causal return window",
    "rv_30m": "thirty-minute causal return window",
    "mom1": "one-minute return ending at scan timestamp",
    "mom3": "three-minute return ending at scan timestamp",
    "mom5": "five-minute return ending at scan timestamp",
    "mom10": "ten-minute return ending at scan timestamp",
    "momentum_acceleration": "difference of causal short lookback returns",
    "vol_ratio_short_long": "causal short/long realized-volatility ratio",
    "crossings": "target crossings observed no later than scan timestamp",
    "reference_gap_bps": "Kalshi target versus proxy window open, both known by scan timestamp",
    "elapsed_fraction": "deterministic window-clock feature",
    "hour_of_day_sin": "deterministic scan-time encoding",
    "hour_of_day_cos": "deterministic scan-time encoding",
    "is_weekend": "deterministic scan-time encoding",
    "asset_BTC": "static asset identity", "asset_ETH": "static asset identity",
    "asset_SOL": "static asset identity", "asset_XRP": "static asset identity",
}


def schema_hash() -> str:
    body = json.dumps({"features": FEATURE_NAMES, "causality": FEATURE_CAUSALITY},
                      sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


def build_causal_features(table: pd.DataFrame, states: pd.DataFrame) -> pd.DataFrame:
    """Construct the exact Step 5 feature matrix without outcome access."""
    if len(table) != len(states) or not table.index.equals(states.index):
        raise ValueError("state/table alignment failure")
    required = {"spot", "kalshi_target", "seconds_remaining", "realized_vol_1m",
                "sigma_remaining_return", "rv_5m", "rv_15m", "rv_30m",
                "mom1", "mom3", "mom5", "mom10", "momentum_acceleration",
                "vol_ratio_short_long", "crossings", "reference_gap_bps",
                "elapsed_fraction", "hour_of_day_sin", "hour_of_day_cos",
                "is_weekend", "asset"}
    missing = required - set(table)
    if missing:
        raise ValueError(f"missing causal feature inputs: {sorted(missing)}")
    target = table.kalshi_target.to_numpy(dtype=float)
    spot = table.spot.to_numpy(dtype=float)
    sigma = table.sigma_remaining_return.to_numpy(dtype=float)
    if np.any(target <= 0) or np.any(sigma <= 0):
        raise ValueError("non-positive target or volatility")
    out = pd.DataFrame(index=table.index)
    out["kalshi_buffer_pct"] = (spot - target) / target
    out["kalshi_normal_z"] = out.kalshi_buffer_pct.to_numpy() / sigma
    out["log_seconds_remaining"] = np.log1p(table.seconds_remaining.to_numpy(dtype=float))
    for name in NUMERIC_FEATURES[3:]:
        out[name] = table[name].to_numpy(dtype=float)
    symbol = table.asset.astype(str).str.replace("-USD", "", regex=False)
    for asset in ("BTC", "ETH", "SOL", "XRP"):
        out[f"asset_{asset}"] = symbol.eq(asset).astype(float)
    if tuple(out.columns) != FEATURE_NAMES:
        raise AssertionError("feature schema order changed")
    if not np.isfinite(out.to_numpy(dtype=float)).all():
        raise ValueError("non-finite causal feature row")
    return out


def assert_feature_schema_safe(names: Iterable[str]) -> None:
    names = tuple(names)
    bad = [name for name in names if any(token in name.lower() for token in FORBIDDEN_FEATURE_TOKENS)]
    if bad:
        raise ValueError(f"future/settlement feature prohibited: {bad}")
    if names != FEATURE_NAMES:
        raise ValueError("feature schema does not match frozen Step 5 allow-list")


def chronological_group_split(groups: pd.Series, window_end: pd.Series,
                              fractions=(.60, .20, .20)) -> dict[str, np.ndarray]:
    """Return row masks; each shared cross-asset window belongs to one split."""
    if len(groups) != len(window_end) or not np.isclose(sum(fractions), 1):
        raise ValueError("invalid grouped split input")
    frame = pd.DataFrame({"group": groups.astype(str), "end": pd.to_datetime(window_end, utc=True)})
    group_end = frame.groupby("group", sort=False).end.first().sort_values(kind="stable")
    unique_windows = sorted(group_end.unique())
    n = len(unique_windows)
    if n < 5:
        raise ValueError("too few windows for chronological three-way split")
    d = int(n * fractions[0]); v = int(n * (fractions[0] + fractions[1]))
    buckets = {
        "development": set(unique_windows[:d]),
        "validation": set(unique_windows[d:v]),
        "sealed_holdout": set(unique_windows[v:]),
    }
    return {name: frame.end.isin(windows).to_numpy() for name, windows in buckets.items()}


class ProbabilityCalibrator:
    def __init__(self, method: str):
        if method not in {"identity", "platt", "isotonic"}:
            raise ValueError("unsupported calibration method")
        self.method = method
        self.model: Any = None

    @staticmethod
    def _logit(p):
        p = np.clip(np.asarray(p, dtype=float), 1e-8, 1 - 1e-8)
        return np.log(p / (1 - p)).reshape(-1, 1)

    def fit(self, probability, y):
        p, y = np.asarray(probability, dtype=float), np.asarray(y, dtype=int)
        if len(p) != len(y) or len(np.unique(y)) != 2 or not np.isfinite(p).all():
            raise ValueError("invalid calibration sample")
        if self.method == "platt":
            self.model = LogisticRegression(C=1.0, solver="lbfgs", random_state=SEED).fit(self._logit(p), y)
        elif self.method == "isotonic":
            self.model = IsotonicRegression(out_of_bounds="clip").fit(p, y)
        return self

    def transform(self, probability):
        p = np.asarray(probability, dtype=float)
        if self.method == "identity":
            return np.clip(p, 1e-8, 1 - 1e-8)
        if self.model is None:
            raise ValueError("calibrator is not fitted")
        if self.method == "platt":
            return self.model.predict_proba(self._logit(p))[:, 1]
        return np.clip(self.model.predict(p), 1e-8, 1 - 1e-8)


def make_base_model(kind: str):
    if kind == "logistic":
        return Pipeline([
            ("scale", StandardScaler()),
            ("model", LogisticRegression(C=1.0, solver="lbfgs", max_iter=1000,
                                         random_state=SEED)),
        ])
    if kind == "hist_gradient_boosting":
        return HistGradientBoostingClassifier(max_depth=3, max_iter=100,
                                              learning_rate=.05, l2_regularization=.1,
                                              random_state=SEED)
    if kind == "statistical":
        return None
    raise ValueError("unsupported model kind")


@dataclass
class FrozenProbabilityModel:
    kind: str
    calibration: str
    base_model: Any
    calibrator: ProbabilityCalibrator
    feature_schema_hash: str
    version: str = STEP5_MODEL_FAMILY

    def predict(self, X: pd.DataFrame, statistical_probability) -> np.ndarray:
        assert_feature_schema_safe(X.columns)
        raw = (np.asarray(statistical_probability, dtype=float) if self.kind == "statistical"
               else self.base_model.predict_proba(X)[:, 1])
        return self.calibrator.transform(raw)


def fit_frozen_model(kind: str, calibration: str,
                     X_fit: pd.DataFrame, y_fit, X_cal: pd.DataFrame, y_cal,
                     statistical_fit, statistical_cal) -> FrozenProbabilityModel:
    assert_feature_schema_safe(X_fit.columns)
    base = make_base_model(kind)
    if base is not None:
        base.fit(X_fit, np.asarray(y_fit, dtype=int))
        cal_raw = base.predict_proba(X_cal)[:, 1]
    else:
        cal_raw = np.asarray(statistical_cal, dtype=float)
    calibrator = ProbabilityCalibrator(calibration).fit(cal_raw, y_cal) if calibration != "identity" else ProbabilityCalibrator("identity")
    return FrozenProbabilityModel(kind, calibration, base, calibrator, schema_hash())


def probability_metrics(y, probability, *, assets=None, entry_mask=None) -> dict:
    y = np.asarray(y, dtype=int); p = np.clip(np.asarray(probability, dtype=float), 1e-12, 1 - 1e-12)
    if len(y) != len(p) or not np.isfinite(p).all():
        raise ValueError("invalid evaluation input")
    mask = np.ones(len(y), dtype=bool) if entry_mask is None else np.asarray(entry_mask, dtype=bool)
    yy, pp = y[mask], p[mask]
    if not len(yy):
        return {"n": 0, "brier": None, "log_loss": None, "accuracy": None, "calibration": []}
    pred = pp >= .5
    rows = {"n": int(len(yy)), "brier": float(np.mean((pp - yy) ** 2)),
            "log_loss": float(-np.mean(yy * np.log(pp) + (1 - yy) * np.log(1 - pp))),
            "accuracy": float(np.mean(pred == yy))}
    bands = [0, .1, .2, .3, .4, .5, .6, .7, .8, .9, .95, .975, 1.000001]
    confidence, correct = np.maximum(pp, 1 - pp), (pred == yy)
    rows["calibration"] = [
        {"band": f"{lo:.3f}-{min(hi,1):.3f}", "n": int(m.sum()),
         "mean_confidence": float(confidence[m].mean()),
         "empirical_accuracy": float(correct[m].mean())}
        for lo, hi in zip(bands[:-1], bands[1:])
        if (m := ((confidence >= lo) & (confidence < hi))).any()
    ]
    rows["yes"] = {"n": int(pred.sum()), "accuracy": float(correct[pred].mean()) if pred.any() else None}
    rows["no"] = {"n": int((~pred).sum()), "accuracy": float(correct[~pred].mean()) if (~pred).any() else None}
    if assets is not None:
        a = np.asarray(assets)[mask]
        rows["per_asset"] = {str(asset): {"n": int((a == asset).sum()),
            "accuracy": float(correct[a == asset].mean()),
            "brier": float(np.mean((pp[a == asset] - yy[a == asset]) ** 2))}
            for asset in sorted(set(a))}
    return rows


def save_model(model: FrozenProbabilityModel, path: Path) -> str:
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, path, compress=3)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_model(path: Path) -> FrozenProbabilityModel:
    model = joblib.load(path)
    if not isinstance(model, FrozenProbabilityModel) or model.feature_schema_hash != schema_hash():
        raise ValueError("Step 5 model/schema mismatch")
    return model
