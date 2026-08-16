from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

from mantis_v4.reference.cf_benchmarks import (BENCHMARK_BY_ASSET, CFBenchmarksResearchProvider,
    CFDataUnavailable, CFObservation)
from mantis_v4.reference.settlement import (SERIES_BY_ASSET, classify_actual_reference,
    classify_bounded_sensitivity, event_specifications, paired_error_metrics,
    reconstruct_ending_average, side_flip, signed_error_bps)

ROOT = Path(__file__).resolve().parents[1]


def observations(end=None, *, count=60, duplicate=False):
    end = end or datetime(2026, 8, 15, 12, 15, tzinfo=UTC)
    rows = [CFObservation(end-timedelta(seconds=60-i), Decimal(100+i)/Decimal(10), "BRTI")
            for i in range(count)]
    if duplicate:
        rows[-1] = rows[-2]
    return rows


class CFProviderTests(unittest.TestCase):
    def test_exact_asset_benchmark_mapping(self):
        self.assertEqual(BENCHMARK_BY_ASSET, {"BTC-USD": "BRTI", "ETH-USD": "ETHUSD_RTI",
                                               "SOL-USD": "SOLUSD_RTI", "XRP-USD": "XRPUSD_RTI"})
        self.assertEqual(set(BENCHMARK_BY_ASSET), set(SERIES_BY_ASSET))

    def test_timestamp_normalized_to_utc(self):
        row = CFObservation(datetime(2026, 1, 1, 7, tzinfo=timezone(timedelta(hours=-5))),
                            Decimal("100"), "BRTI")
        self.assertEqual(row.timestamp_utc, datetime(2026, 1, 1, 12, tzinfo=UTC))

    def test_unknown_benchmark_and_naive_time_rejected(self):
        with self.assertRaises(ValueError):
            CFObservation(datetime.now(), Decimal("1"), "UNKNOWN")
        with self.assertRaises(ValueError):
            CFObservation(datetime.now(), Decimal("1"), "BRTI")

    def test_missing_authorized_retriever_fails_without_yahoo_fallback(self):
        with self.assertRaises(CFDataUnavailable):
            CFBenchmarksResearchProvider().observations(
                asset="BTC-USD", start_utc=datetime(2026, 1, 1, tzinfo=UTC),
                end_utc=datetime(2026, 1, 1, 0, 1, tzinfo=UTC))

    def test_provider_rejects_duplicates_and_wrong_identity(self):
        start = datetime(2026, 1, 1, tzinfo=UTC); end = start+timedelta(minutes=1)
        with self.assertRaisesRegex(ValueError, "duplicated"):
            CFBenchmarksResearchProvider(lambda *x: observations(end, duplicate=True)).observations(
                asset="BTC-USD", start_utc=start, end_utc=end)
        with self.assertRaisesRegex(ValueError, "invalid benchmark"):
            CFBenchmarksResearchProvider(lambda *x: [CFObservation(start, Decimal(1), "ETHUSD_RTI")]).observations(
                asset="BTC-USD", start_utc=start, end_utc=end)


class SettlementReconstructionTests(unittest.TestCase):
    def test_specs_encode_series_benchmark_equality_and_partial_status(self):
        specs = event_specifications(datetime(2026, 8, 15, tzinfo=UTC))
        self.assertEqual(set(specs), set(SERIES_BY_ASSET))
        for asset, spec in specs.items():
            self.assertEqual(spec.benchmark_identifier, BENCHMARK_BY_ASSET[asset])
            self.assertEqual((spec.ending_window_seconds, spec.observation_frequency_seconds,
                              spec.expected_observation_count), (60, 1, 60))
            self.assertEqual((spec.comparison_operator, spec.equality_outcome), (">=", "YES"))
            self.assertEqual(spec.verification_status, "PARTIALLY_VERIFIED")
        self.assertEqual([specs[a].final_round_digits for a in SERIES_BY_ASSET], [2, 2, 4, 4])

    def test_exact_minute_boundary_and_arithmetic_mean(self):
        end = datetime(2026, 8, 15, 12, 15, tzinfo=UTC); rows = observations(end)
        result = reconstruct_ending_average(rows, window_end_utc=end, target=Decimal("12.95"))
        self.assertEqual(result.first_timestamp_utc, end-timedelta(seconds=60))
        self.assertEqual(result.last_timestamp_utc, end-timedelta(seconds=1))
        self.assertEqual(result.ending_average, Decimal("12.95"))
        self.assertTrue(result.outcome_yes)  # equality settles YES
        self.assertEqual(result.terminal_observation, Decimal("15.9"))

    def test_missing_duplicate_and_out_of_boundary_fail_closed(self):
        end = datetime(2026, 8, 15, 12, 15, tzinfo=UTC)
        with self.assertRaisesRegex(ValueError, "incomplete"):
            reconstruct_ending_average(observations(end, count=59), window_end_utc=end, target=Decimal(1))
        with self.assertRaisesRegex(ValueError, "duplicated"):
            reconstruct_ending_average(observations(end, duplicate=True), window_end_utc=end, target=Decimal(1))
        shifted = observations(end); shifted[0] = CFObservation(end-timedelta(seconds=61), Decimal(10), "BRTI")
        with self.assertRaisesRegex(ValueError, "exactly cover"):
            reconstruct_ending_average(shifted, window_end_utc=end, target=Decimal(1))

    def test_reconstruction_is_deterministic(self):
        end = datetime(2026, 8, 15, 12, 15, tzinfo=UTC); rows = observations(end)
        self.assertEqual(reconstruct_ending_average(rows, window_end_utc=end, target=Decimal(10)),
                         reconstruct_ending_average(rows, window_end_utc=end, target=Decimal(10)))


class ReferenceMeasurementTests(unittest.TestCase):
    def test_signed_and_absolute_basis_point_math(self):
        np.testing.assert_allclose(signed_error_bps([101, 99], [100, 100]), [100, -100])
        frame = pd.DataFrame({"proxy": [101., 99.], "official": [100., 100.], "target": [100., 100.]})
        metrics = paired_error_metrics(frame, proxy_col="proxy", official_col="official", target_col="target")
        self.assertEqual(metrics["mean_signed_bps"], 0)
        self.assertEqual(metrics["median_absolute_bps"], 100)
        self.assertEqual(metrics["target_side_flip_rate"], .5)

    def test_side_flip_and_equality_semantics(self):
        np.testing.assert_array_equal(side_flip([99, 100], [100, 99], [100, 100]), [True, True])
        self.assertEqual(classify_actual_reference(proxy_value=Decimal(99), official_value=Decimal(100),
                                                   target=Decimal(100)), "REFERENCE_FLIPPED")
        self.assertEqual(classify_actual_reference(proxy_value=None, official_value=None,
                                                   target=Decimal(100)), "REFERENCE_UNKNOWN")

    def test_bounded_sensitivity_never_claims_actual_flip(self):
        self.assertEqual(classify_bounded_sensitivity(value=Decimal("100.02"), target=Decimal(100),
                                                      error_bound_bps=Decimal(5)), "REFERENCE_AMBIGUOUS")
        self.assertEqual(classify_bounded_sensitivity(value=Decimal("100.20"), target=Decimal(100),
                                                      error_bound_bps=Decimal(5)), "REFERENCE_ROBUST")


class FrozenBoundaryTests(unittest.TestCase):
    def test_exact_step4_holdout_cohort_artifact_is_unchanged(self):
        summary = json.loads((ROOT / "data/kalshi_step5_summary.json").read_text())
        cohort = summary["sealed_holdout"]["baseline_fixed_step4_entry_cohort"]
        self.assertEqual(cohort["n"], 1377)
        self.assertAlmostEqual(summary["sealed_holdout"]["fixed_step4_entry_coverage"], 1377/2111)

    def test_no_live_ensemble_execution_or_exit_integration(self):
        paths = [ROOT/"mantis_v4/reference/cf_benchmarks.py", ROOT/"mantis_v4/reference/settlement.py",
                 ROOT/"scripts/kalshi_settlement_reference_study.py"]
        source = "\n".join(path.read_text(encoding="utf-8") for path in paths)
        for forbidden in ("mantis_v4.ui", "mantis_v4.forward", "PRIMARY_SELECTOR_V1",
                          "ENTER YES", "ENTER NO", "take_profit", "stop_loss", "place_order"):
            self.assertNotIn(forbidden, source)
        self.assertIn('"gates_modified": False', source)
        self.assertIn('"step5_model_modified": False', source)
        self.assertIn('"ensemble_created": False', source)

    def test_step5_model_hash_remains_frozen(self):
        import hashlib
        manifest = json.loads((ROOT/"data/kalshi_step5_freeze.json").read_text())
        digest = hashlib.sha256((ROOT/"data/models/kalshi_step5_frozen.joblib").read_bytes()).hexdigest()
        self.assertEqual(digest, manifest["model_hash"])


if __name__ == "__main__":
    unittest.main()
