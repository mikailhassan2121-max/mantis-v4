from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

from mantis_v4.reference.kalshi_reference_risk import (CURRENT_VALUE_PROVENANCE,
    EVENT_REFERENCE_PROVENANCE, FIXED_BOUNDS_BPS, OPENED_HOLDOUT_LABEL,
    POLICY_VERSION, assess_reference_risk, evaluate_shadow_cohort,
    fit_empirical_bounds, format_shadow_diagnostic, near_target_bucket,
    reference_distance_bps, time_remaining_bucket)

ROOT = Path(__file__).resolve().parents[1]


class ReferenceRiskMathTests(unittest.TestCase):
    def test_version_and_provenance_are_explicit(self):
        self.assertEqual(POLICY_VERSION, "KALSHI_REFERENCE_RISK_V1_SHADOW")
        self.assertEqual(EVENT_REFERENCE_PROVENANCE, "KALSHI_CRYPTO15M_CF_BENCHMARKS")
        self.assertEqual(CURRENT_VALUE_PROVENANCE, "YAHOO_PROXY")
        self.assertIn("PREVIOUSLY_OPENED", OPENED_HOLDOUT_LABEL)

    def test_reference_gap_basis_points_uses_actual_target(self):
        self.assertEqual(reference_distance_bps(Decimal("100.05"), Decimal("100")), Decimal("5.00"))
        with self.assertRaises(ValueError):
            reference_distance_bps(Decimal("100"), Decimal("0"))

    def test_fixed_sensitivity_bounds_are_predeclared(self):
        self.assertEqual(FIXED_BOUNDS_BPS, tuple(map(Decimal, ("1", "2", "5", "10"))))
        for bound in FIXED_BOUNDS_BPS:
            robust = assess_reference_risk(asset="BTC-USD", target=Decimal(100),
                proxy_current=Decimal("100.20"), uncertainty_bound_bps=bound)
            self.assertEqual(robust.classification, "REFERENCE_ROBUST")

    def test_boundary_crossing_is_ambiguous_not_flipped(self):
        result = assess_reference_risk(asset="BTC-USD", target=Decimal(100),
            proxy_current=Decimal("100.02"), uncertainty_bound_bps=Decimal(5))
        self.assertEqual(result.classification, "REFERENCE_AMBIGUOUS")
        self.assertEqual((result.perturbed_low_side, result.perturbed_high_side), ("NO", "YES"))

    def test_missing_input_is_unknown_and_never_robust(self):
        for kwargs in ({"target": None, "proxy_current": Decimal(1), "uncertainty_bound_bps": Decimal(1)},
                       {"target": Decimal(1), "proxy_current": None, "uncertainty_bound_bps": Decimal(1)},
                       {"target": Decimal(1), "proxy_current": Decimal(1), "uncertainty_bound_bps": None}):
            self.assertEqual(assess_reference_risk(asset="XRP-USD", **kwargs).classification,
                             "REFERENCE_UNKNOWN")

    def test_near_target_and_time_buckets_are_fixed(self):
        self.assertEqual([near_target_bucket(x) for x in (.5, 1, 2, 5, 10)],
                         ["LT_1BP", "1_2BP", "2_5BP", "5_10BP", "GE_10BP"])
        self.assertEqual([time_remaining_bucket(x) for x in (30, 90, 150, 210, 270, 301)],
                         ["T_0_60", "T_60_120", "T_120_180", "T_180_240", "T_240_300", "PRE_ENTRY_GT_300"])


class EmpiricalBoundTests(unittest.TestCase):
    def test_empirical_p50_p95_p99_are_deterministic(self):
        rows = []
        for asset in ("BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD"):
            for i in range(100):
                rows.append({"asset": asset, "kalshi_target": 100., "yahoo_start_proxy": 100*(1+(i-50)/100000)})
        frame = pd.DataFrame(rows)
        self.assertEqual(fit_empirical_bounds(frame), fit_empirical_bounds(frame.copy()))
        self.assertEqual(set(fit_empirical_bounds(frame)["BTC-USD"]), {"P50", "P95", "P99"})

    def test_insufficient_empirical_data_fails_closed(self):
        with self.assertRaises(ValueError):
            fit_empirical_bounds(pd.DataFrame([{"asset": "BTC-USD", "kalshi_target": 1,
                                                "yahoo_start_proxy": 1}]))


class CohortAndDiagnosticTests(unittest.TestCase):
    def test_cohort_reports_robust_ambiguous_unknown_accuracy_and_sides(self):
        frame = pd.DataFrame({"asset": ["BTC-USD"]*3, "spot": [101., 99., np.nan],
                              "kalshi_target": [100.]*3, "outcome_yes": [1, 0, 1],
                              "p_yes": [.9, .1, .9]})
        result = evaluate_shadow_cohort(frame, {"BTC-USD": Decimal(5)}, eligible_contracts=4)
        self.assertEqual((result["REFERENCE_ROBUST"], result["REFERENCE_UNKNOWN"]), (2, 1))
        self.assertEqual(result["accuracy"], 1.)
        self.assertEqual((result["yes"]["n"], result["no"]["n"]), (1, 1))

    def test_shadow_diagnostic_is_explicitly_non_actionable(self):
        assessment = assess_reference_risk(asset="BTC-USD", target=Decimal(100),
            proxy_current=Decimal(101), uncertainty_bound_bps=Decimal(5))
        text = format_shadow_diagnostic(assessment)
        self.assertIn("KALSHI REFERENCE SHADOW", text)
        self.assertIn("ACTIONABILITY ........ SHADOW ONLY", text)
        self.assertNotIn("ENTER", text)


class ProtectedBoundaryTests(unittest.TestCase):
    def test_step8_sources_have_no_production_connections(self):
        paths = [ROOT/"mantis_v4/reference/kalshi_reference_risk.py",
                 ROOT/"scripts/kalshi_reference_risk_study.py"]
        source = "\n".join(path.read_text(encoding="utf-8") for path in paths)
        for forbidden in ("mantis_v4.ui", "mantis_v4.forward", "PRIMARY_SELECTOR",
                          "mantis_v4.economics.decision", "speak(", "place_order"):
            self.assertNotIn(forbidden, source)
        for invariant in ('"normal_z_modified": False', '"gates_modified": False',
                          '"step5_model_retrained": False', '"ensemble_created": False'):
            self.assertIn(invariant, source)

    def test_original_gates_are_unchanged(self):
        from mantis_v4.economics.kalshi_reference import TRANSFERRED_POLICY
        self.assertEqual(TRANSFERRED_POLICY.__dict__, {
            "name": "KALSHI_H_V1", "probability_threshold": .95, "lcb_threshold": .90,
            "max_fragility": 50, "max_disagreement": .05, "max_crossing_probability": .35,
            "max_seconds_remaining": 300, "min_seconds_remaining": 0., "min_abs_z": 0.,
            "early_wait_seconds": 0., "no_trade_seconds": 0., "max_crossings": 4})

    def test_frozen_step5_model_hash_unchanged(self):
        import hashlib
        manifest = json.loads((ROOT/"data/kalshi_step5_freeze.json").read_text())
        self.assertEqual(hashlib.sha256((ROOT/"data/models/kalshi_step5_frozen.joblib").read_bytes()).hexdigest(),
                         manifest["model_hash"])


if __name__ == "__main__":
    unittest.main()
