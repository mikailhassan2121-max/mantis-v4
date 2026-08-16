from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from mantis_v4.models.kalshi_step5 import schema_hash
from mantis_v4.models.kalshi_step6 import (
    DISAGREEMENT_LABELS, MODEL_COMPLEMENTARITY_POLICY, OPENED_HOLDOUT_LABEL,
    analyze_split, apply_regimes, build_comparison_states, error_complementarity,
    fit_development_regimes, incremental_information, verify_frozen_artifacts,
)

ROOT = Path(__file__).resolve().parents[1]


def sample(n=24):
    starts = pd.date_range("2026-01-01", periods=n // 4, freq="15min", tz="UTC").repeat(4)
    assets = ["BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD"] * (n // 4)
    p_stat = np.linspace(.05, .95, n)
    p_ml = np.clip(p_stat + np.tile([-.08, -.02, .02, .08], n // 4), .01, .99)
    y = (np.arange(n) % 3 != 0).astype(int)
    table = pd.DataFrame({
        "contract_id": [f"w{i//4}" for i in range(n)], "asset": assets,
        "group_key": [f"w{i//4}" for i in range(n)], "scan_utc": starts + pd.Timedelta(minutes=10),
        "window_start_utc": starts, "window_end_utc": starts + pd.Timedelta(minutes=15),
        "seconds_remaining": np.tile([800, 500, 200, 30], n // 4),
        "kalshi_target": np.full(n, 100.), "spot": np.linspace(99, 101, n),
        "outcome_yes": y, "realized_vol_1m": np.linspace(.001, .003, n),
        "sigma_remaining_return": np.full(n, .01), "crossings": np.arange(n) % 4,
        "reference_gap_bps": np.linspace(-10, 10, n),
    })
    states = pd.DataFrame({"p_yes": p_stat, "normal_z": np.linspace(-2.5, 2.5, n)})
    return table, states, p_ml, np.arange(n) % 5 == 0


class Step6StateTests(unittest.TestCase):
    def test_policy_is_separate_shadow_identity(self):
        self.assertEqual(MODEL_COMPLEMENTARITY_POLICY, "KALSHI_COMPLEMENTARITY_V1")
        self.assertEqual(OPENED_HOLDOUT_LABEL, "PREVIOUSLY_OPENED_HOLDOUT_NOT_STEP6_SEALED")

    def test_exact_probability_and_selected_confidence_semantics(self):
        table, states, ml, entry = sample()
        out = build_comparison_states(table, states, ml, entry)
        np.testing.assert_allclose(out.signed_disagreement, ml - states.p_yes)
        np.testing.assert_allclose(out.abs_disagreement, np.abs(ml - states.p_yes))
        np.testing.assert_allclose(out.stat_selected_confidence, np.maximum(states.p_yes, 1-states.p_yes))
        self.assertTrue((out.model_direction_agree == ((states.p_yes >= .5) == (ml >= .5))).all())

    def test_probability_of_correct_side_uses_yes_semantics(self):
        table, states, ml, entry = sample()
        out = build_comparison_states(table, states, ml, entry)
        expected = np.where(table.outcome_yes == 1, states.p_yes, 1-states.p_yes)
        np.testing.assert_allclose(out.stat_probability_correct_side, expected)

    def test_alignment_and_malformed_data_fail_closed(self):
        table, states, ml, entry = sample()
        with self.assertRaisesRegex(ValueError, "alignment"):
            build_comparison_states(table, states.iloc[:-1], ml[:-1], entry[:-1])
        ml[0] = np.nan
        with self.assertRaisesRegex(ValueError, "non-finite"):
            build_comparison_states(table, states, ml, entry)

    def test_row_identity_is_unique_and_deterministic(self):
        args = sample()
        a, b = build_comparison_states(*args), build_comparison_states(*args)
        self.assertFalse(a.row_id.duplicated().any())
        self.assertEqual(a.row_id.tolist(), b.row_id.tolist())


class Step6AnalysisTests(unittest.TestCase):
    def setUp(self):
        table, states, ml, entry = sample()
        self.base = build_comparison_states(table, states, ml, entry)
        self.thresholds = fit_development_regimes(self.base.iloc[:16])
        self.frame = apply_regimes(self.base, self.thresholds)

    def test_regime_thresholds_fit_development_and_apply_deterministically(self):
        self.assertEqual(self.thresholds, fit_development_regimes(self.base.iloc[:16]))
        pd.testing.assert_frame_equal(self.frame, apply_regimes(self.base, self.thresholds))

    def test_fixed_disagreement_buckets_are_predeclared(self):
        self.assertEqual(tuple(self.frame.disagreement_bucket.cat.categories), DISAGREEMENT_LABELS)

    def test_report_is_deterministic_and_has_required_analyses(self):
        a, b = analyze_split(self.frame), analyze_split(self.frame)
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))
        for key in ("direction", "probability_disagreement", "high_confidence_agreement",
                    "skepticism", "error_complementarity", "direction_disagreement_rescue",
                    "regimes", "conditional_calibration", "clustered_accuracy"):
            self.assertIn(key, a)

    def test_error_cells_are_exhaustive(self):
        result = error_complementarity(self.frame)
        total = sum(result[k] for k in ("both_correct", "both_wrong", "stat_only_correct", "ml_only_correct"))
        self.assertAlmostEqual(total, 1.)

    def test_incremental_model_is_diagnostic_and_development_fitted(self):
        result = incremental_information(self.frame.iloc[:16], self.frame.iloc[16:])
        self.assertIn("delta_extended_minus_base", result)
        self.assertEqual(set(result["plus_ml_disagreement"]["coefficients"]),
                         {"stat_selected_confidence", "ml_selected_confidence",
                          "signed_disagreement", "abs_disagreement"})


class Step6FrozenBoundaryTests(unittest.TestCase):
    def test_frozen_manifest_and_model_hash_verify(self):
        manifest = ROOT / "data" / "kalshi_step5_freeze.json"
        model = ROOT / "data" / "models" / "kalshi_step5_frozen.joblib"
        loaded = verify_frozen_artifacts(manifest, model, schema_hash=schema_hash())
        self.assertEqual(loaded["feature_schema_hash"], schema_hash())

    def test_tampered_model_fails_closed(self):
        manifest = ROOT / "data" / "kalshi_step5_freeze.json"
        model = ROOT / "data" / "models" / "kalshi_step5_frozen.joblib"
        with patch("mantis_v4.models.kalshi_step6.sha256_file", return_value="0" * 64):
            with self.assertRaisesRegex(ValueError, "model_hash"):
                verify_frozen_artifacts(manifest, model, schema_hash=schema_hash())

    def test_step6_has_no_live_or_actionability_imports(self):
        source = (ROOT / "mantis_v4/models/kalshi_step6.py").read_text(encoding="utf-8")
        for forbidden in ("mantis_v4.ui", "mantis_v4.forward", "PRIMARY_SELECTOR",
                          "EventBus", "speak(", "ENTER YES", "ENTER NO"):
            self.assertNotIn(forbidden, source)

    def test_no_ensemble_gate_execution_or_position_management_created(self):
        source = (ROOT / "scripts/kalshi_complementarity_study.py").read_text(encoding="utf-8")
        for asserted in ('"ensemble_created": False', '"gates_modified": False',
                         '"execution_integration": False', '"position_management_integration": False'):
            self.assertIn(asserted, source)
        self.assertNotIn("weighted_average", source)

    def test_original_step4_gates_remain_exact(self):
        from mantis_v4.economics.kalshi_reference import TRANSFERRED_POLICY
        self.assertEqual((TRANSFERRED_POLICY.probability_threshold, TRANSFERRED_POLICY.lcb_threshold,
                          TRANSFERRED_POLICY.max_fragility, TRANSFERRED_POLICY.max_disagreement,
                          TRANSFERRED_POLICY.max_seconds_remaining, TRANSFERRED_POLICY.max_crossing_probability,
                          TRANSFERRED_POLICY.max_crossings), (.95, .90, 50., .05, 300, .35, 4))


if __name__ == "__main__":
    unittest.main()
