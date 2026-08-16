from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from mantis_v4.models.kalshi_step5 import (
    FEATURE_NAMES, ProbabilityCalibrator, assert_feature_schema_safe,
    build_causal_features, chronological_group_split, fit_frozen_model,
    load_model, probability_metrics, save_model, schema_hash,
)


def feature_inputs(n=8):
    assets = ["BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD"] * (n // 4)
    table = pd.DataFrame({
        "spot": np.linspace(100, 101, n), "kalshi_target": np.full(n, 100.),
        "seconds_remaining": np.linspace(300, 30, n), "realized_vol_1m": np.full(n, .001),
        "sigma_remaining_return": np.full(n, .005), "rv_5m": np.full(n, .0011),
        "rv_15m": np.full(n, .0012), "rv_30m": np.full(n, .0013),
        "mom1": np.linspace(-.001, .001, n), "mom3": np.zeros(n), "mom5": np.zeros(n),
        "mom10": np.zeros(n), "momentum_acceleration": np.zeros(n),
        "vol_ratio_short_long": np.ones(n), "crossings": np.arange(n) % 2,
        "reference_gap_bps": np.linspace(-2, 2, n), "elapsed_fraction": np.linspace(.1, .9, n),
        "hour_of_day_sin": np.zeros(n), "hour_of_day_cos": np.ones(n), "is_weekend": np.zeros(n),
        "asset": assets,
    })
    return table, pd.DataFrame(index=table.index)


class Step5FeatureTests(unittest.TestCase):
    def test_feature_construction_is_deterministic_and_exactly_allowlisted(self):
        table, states = feature_inputs()
        a, b = build_causal_features(table, states), build_causal_features(table.copy(), states.copy())
        pd.testing.assert_frame_equal(a, b)
        self.assertEqual(tuple(a.columns), FEATURE_NAMES)
        self.assertEqual(schema_hash(), schema_hash())

    def test_settlement_future_and_label_fields_cannot_enter_schema(self):
        for name in ("settlement_value", "future_high", "outcome_yes", "terminal_price", "result"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                assert_feature_schema_safe((*FEATURE_NAMES[:-1], name))

    def test_nonfinite_and_bad_alignment_fail_closed(self):
        table, states = feature_inputs()
        table.loc[0, "mom1"] = np.nan
        with self.assertRaisesRegex(ValueError, "non-finite"):
            build_causal_features(table, states)
        table, states = feature_inputs()
        with self.assertRaisesRegex(ValueError, "alignment"):
            build_causal_features(table, states.iloc[:-1])


class Step5SplitTests(unittest.TestCase):
    def test_shared_window_assets_never_cross_splits(self):
        ends, groups = [], []
        for i, end in enumerate(pd.date_range("2026-01-01", periods=10, freq="15min", tz="UTC")):
            for asset in range(4):
                groups.append(f"w{i}"); ends.append(end)
        masks = chronological_group_split(pd.Series(groups), pd.Series(ends))
        for group in set(groups):
            memberships = [name for name, mask in masks.items()
                           if mask[np.array(groups) == group].any()]
            self.assertEqual(len(memberships), 1)
        self.assertLess(pd.Series(ends)[masks["development"]].max(),
                        pd.Series(ends)[masks["validation"]].min())
        self.assertLess(pd.Series(ends)[masks["validation"]].max(),
                        pd.Series(ends)[masks["sealed_holdout"]].min())


class Step5CalibrationTests(unittest.TestCase):
    def test_platt_and_isotonic_are_fitted_only_from_supplied_sample(self):
        p = np.linspace(.05, .95, 40); y = (p > .6).astype(int)
        for method in ("platt", "isotonic"):
            calibrated = ProbabilityCalibrator(method).fit(p, y).transform(p)
            self.assertTrue(np.isfinite(calibrated).all())
            self.assertTrue(((calibrated > 0) & (calibrated < 1)).all())
        with self.assertRaises(ValueError):
            ProbabilityCalibrator("platt").fit([.1, .2], [1, 1])

    def test_model_serialization_is_versioned_and_reproducible(self):
        table, states = feature_inputs(8); X = build_causal_features(table, states)
        y = np.array([0, 0, 1, 1, 0, 0, 1, 1]); stat = np.linspace(.1, .9, 8)
        model = fit_frozen_model("logistic", "platt", X.iloc[:4], y[:4],
                                 X.iloc[4:], y[4:], stat[:4], stat[4:])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "model.joblib"
            digest = save_model(model, path)
            loaded = load_model(path)
            self.assertEqual(len(digest), 64)
            np.testing.assert_allclose(model.predict(X, stat), loaded.predict(X, stat))
            self.assertEqual(loaded.feature_schema_hash, schema_hash())

    def test_probability_metrics_reports_calibration_and_assets(self):
        result = probability_metrics([0, 0, 1, 1], [.1, .4, .6, .9],
                                     assets=["BTC", "ETH", "BTC", "ETH"])
        self.assertEqual(result["n"], 4)
        self.assertIn("brier", result); self.assertIn("log_loss", result)
        self.assertTrue(result["calibration"]); self.assertEqual(set(result["per_asset"]), {"BTC", "ETH"})


if __name__ == "__main__":
    unittest.main()
