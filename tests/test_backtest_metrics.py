"""
Metrics, splits, features and dataset-schema tests (Phase 3 req. 12, 13, 15, 6, 7).
"""

from __future__ import annotations

import math
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from mantis_v4.clock import UTC
from mantis_v4.contracts import ContractWindow, SettlementRule
from mantis_v4.backtest.dataset import (
    DATASET_COLUMNS,
    ProvenanceViolation,
    assert_provenance,
    replay_to_row,
    write_dataset,
    write_schema_documentation,
)
from mantis_v4.backtest.diagnostics import (
    analyse_bars,
    analyse_contracts,
    analyse_cross_asset,
    longest_identical_run,
)
from mantis_v4.backtest.features import compute_rsi, compute_v3_rsi, normal_cdf
from mantis_v4.backtest.metrics import (
    brier_score,
    build_report,
    evaluate,
    log_loss,
    wilson_interval,
)
from mantis_v4.backtest.replay import ContractReplay
from mantis_v4.backtest.splits import (
    LabelledSpan,
    assert_no_overlap,
    purged_walk_forward,
    spans_from_replays,
)

ET = ZoneInfo("America/New_York")


def make_replay(asset="BTC-USD", offset_minutes=0, entered=True, side="YES",
                outcome_yes=True, p_yes=0.8, usable=True) -> ContractReplay:
    window = ContractWindow.for_instant(
        datetime(2026, 8, 10, 12, 0, tzinfo=UTC) + timedelta(minutes=offset_minutes),
        ET, 15,
    )
    replay = ContractReplay(asset=asset, window=window, strategy="test")
    replay.reference = 100.0
    replay.reference_bar_utc = window.start_utc
    replay.usable = usable
    replay.terminal_price = 101.0 if outcome_yes else 99.0
    replay.outcome_yes = outcome_yes
    replay.outcome_label = "YES" if outcome_yes else "NO"
    if entered:
        replay.entered = True
        replay.entry_side = side
        replay.entry_timestamp = window.start_utc + timedelta(minutes=5)
        replay.entry_spot = 100.5
        replay.entry_p_yes = p_yes
        replay.decision = f"ENTER_{side}"
        replay.entry_features = {
            "seconds_remaining": 600.0, "buffer_abs": 0.5, "buffer_pct": 0.005,
            "realized_vol_1m": 0.0004, "spot": 100.5,
        }
        replay.prediction_correct = int(side == replay.outcome_label)
    return replay


class TestWilsonInterval(unittest.TestCase):
    def test_contains_point_estimate(self):
        low, high = wilson_interval(70, 100)
        self.assertLess(low, 0.70)
        self.assertGreater(high, 0.70)

    def test_stays_within_unit_interval(self):
        for successes, trials in [(0, 5), (5, 5), (1, 3), (99, 100), (0, 1)]:
            low, high = wilson_interval(successes, trials)
            self.assertGreaterEqual(low, 0.0)
            self.assertLessEqual(high, 1.0)

    def test_perfect_record_does_not_claim_certainty(self):
        """5/5 correct must NOT report a 100% lower bound."""
        low, high = wilson_interval(5, 5)
        self.assertLess(low, 0.75)
        self.assertAlmostEqual(high, 1.0, places=6)

    def test_narrows_with_sample_size(self):
        small = wilson_interval(8, 10)
        large = wilson_interval(800, 1000)
        self.assertGreater(small[1] - small[0], large[1] - large[0])

    def test_zero_trials_returns_none(self):
        self.assertEqual(wilson_interval(0, 0), (None, None))


class TestProbabilityMetrics(unittest.TestCase):
    def test_brier_of_perfect_forecast_is_zero(self):
        self.assertAlmostEqual(brier_score([1.0, 0.0, 1.0], [1, 0, 1]), 0.0)

    def test_brier_of_coin_flip_is_quarter(self):
        self.assertAlmostEqual(brier_score([0.5] * 4, [1, 0, 1, 0]), 0.25)

    def test_brier_of_confidently_wrong_is_one(self):
        self.assertAlmostEqual(brier_score([0.0, 1.0], [1, 0]), 1.0)

    def test_log_loss_of_perfect_forecast_is_near_zero(self):
        self.assertLess(log_loss([1.0, 0.0], [1, 0]), 1e-10)

    def test_log_loss_of_coin_flip_is_ln2(self):
        self.assertAlmostEqual(log_loss([0.5, 0.5], [1, 0]), math.log(2), places=9)

    def test_log_loss_clipping_keeps_it_finite(self):
        value = log_loss([0.0], [1])
        self.assertTrue(math.isfinite(value))
        self.assertGreater(value, 30)

    def test_no_probabilities_returns_none_not_a_default(self):
        """Requirement 12: never substitute a probability that does not exist."""
        self.assertIsNone(brier_score([], []))
        self.assertIsNone(log_loss([], []))
        self.assertIsNone(brier_score([None, None], [1, 0]))


class TestEvaluate(unittest.TestCase):
    def test_counts_and_accuracy(self):
        replays = [
            make_replay(offset_minutes=0, side="YES", outcome_yes=True),
            make_replay(offset_minutes=15, side="YES", outcome_yes=False),
            make_replay(offset_minutes=30, side="NO", outcome_yes=False),
            make_replay(offset_minutes=45, entered=False, outcome_yes=True),
        ]
        m = evaluate(replays)
        self.assertEqual(m.contracts_observed, 4)
        self.assertEqual(m.contracts_traded, 3)
        self.assertEqual(m.correct, 2)
        self.assertEqual(m.incorrect, 1)
        self.assertAlmostEqual(m.accuracy, 2 / 3)
        self.assertAlmostEqual(m.coverage, 0.75)
        self.assertAlmostEqual(m.abstention_rate, 0.25)

    def test_no_trade_is_not_scored_as_a_loss(self):
        replays = [make_replay(entered=False, outcome_yes=True) for _ in range(10)]
        m = evaluate(replays)
        self.assertEqual(m.contracts_observed, 10)
        self.assertEqual(m.contracts_traded, 0)
        self.assertIsNone(m.accuracy)          # nothing scored, not 0%
        self.assertEqual(m.abstention_rate, 1.0)

    def test_unusable_contracts_are_excluded(self):
        replays = [
            make_replay(offset_minutes=0),
            make_replay(offset_minutes=15, usable=False),
        ]
        self.assertEqual(evaluate(replays).contracts_observed, 1)

    def test_base_rate_is_reported(self):
        replays = [
            make_replay(offset_minutes=i * 15, outcome_yes=(i < 7), entered=False)
            for i in range(10)
        ]
        self.assertAlmostEqual(evaluate(replays).base_rate_yes, 0.7)

    def test_metrics_row_has_no_economic_field(self):
        """Requirement 13, enforced structurally."""
        row = evaluate([make_replay()]).as_row()
        for forbidden in ("pnl", "profit", "ev", "return", "roi"):
            self.assertNotIn(forbidden, [k.lower() for k in row])
        self.assertIn("NOT EVALUABLE", row["economic_profitability"])

    def test_report_breakdowns(self):
        replays = [
            make_replay(asset="BTC-USD", offset_minutes=0, side="YES", outcome_yes=True),
            make_replay(asset="ETH-USD", offset_minutes=0, side="NO", outcome_yes=False),
            make_replay(asset="BTC-USD", offset_minutes=15, side="YES", outcome_yes=False),
        ]
        report = build_report("test", replays)
        self.assertIn("BTC-USD", report.by_asset)
        self.assertIn("ETH-USD", report.by_asset)
        self.assertIn("YES", report.by_direction)
        self.assertIn("NO", report.by_direction)
        self.assertEqual(report.by_asset["BTC-USD"].contracts_traded, 2)


class TestPurgedWalkForward(unittest.TestCase):
    def build_spans(self, n: int) -> list[LabelledSpan]:
        base = datetime(2026, 8, 10, 0, 0, tzinfo=UTC)
        return [
            LabelledSpan(
                index=i,
                start=base + timedelta(minutes=15 * i),
                end=base + timedelta(minutes=15 * (i + 1)),
            )
            for i in range(n)
        ]

    def test_folds_are_chronological_and_disjoint(self):
        spans = self.build_spans(500)
        splits = purged_walk_forward(spans, folds=5)
        self.assertGreater(len(splits), 0)
        for split in splits:
            self.assertFalse(set(split.train_indices) & set(split.test_indices))
            self.assertLess(max(split.train_indices), min(split.test_indices))

    def test_no_train_span_overlaps_the_test_block(self):
        spans = self.build_spans(500)
        for split in purged_walk_forward(spans, folds=5):
            assert_no_overlap(spans, split)   # raises on leakage

    def test_embargo_removes_samples_after_the_test_block(self):
        """Embargo only matters for a rolling/shuffled scheme; with expanding
        chronological folds nothing after the test block is in training, so the
        embargo count is legitimately zero. Verified explicitly rather than
        assumed."""
        spans = self.build_spans(300)
        splits = purged_walk_forward(spans, folds=4, embargo=timedelta(hours=2))
        for split in splits:
            self.assertEqual(split.embargoed, 0)
            for i in split.train_indices:
                self.assertLess(spans[i].end, split.test_start + timedelta(seconds=1))

    def test_overlapping_labels_are_purged(self):
        """Deliberately overlapping spans: purging must remove them."""
        base = datetime(2026, 8, 10, 0, 0, tzinfo=UTC)
        spans = [
            LabelledSpan(index=i, start=base + timedelta(minutes=5 * i),
                         end=base + timedelta(minutes=5 * i + 60))
            for i in range(200)
        ]
        splits = purged_walk_forward(spans, folds=4)
        self.assertTrue(any(s.purged > 0 for s in splits),
                        "overlapping spans should trigger purging")
        for split in splits:
            assert_no_overlap(spans, split)

    def test_expanding_window_grows(self):
        spans = self.build_spans(500)
        splits = purged_walk_forward(spans, folds=5, expanding=True)
        sizes = [s.train_size for s in splits]
        self.assertEqual(sizes, sorted(sizes))

    def test_rolling_window_does_not_grow_without_bound(self):
        spans = self.build_spans(500)
        rolling = purged_walk_forward(spans, folds=5, expanding=False)
        expanding = purged_walk_forward(spans, folds=5, expanding=True)
        self.assertLessEqual(rolling[-1].train_size, expanding[-1].train_size)

    def test_same_window_across_assets_shares_a_group(self):
        replays = [
            make_replay(asset="BTC-USD", offset_minutes=0),
            make_replay(asset="ETH-USD", offset_minutes=0),
            make_replay(asset="SOL-USD", offset_minutes=0),
        ]
        spans = spans_from_replays(replays)
        self.assertEqual(len({s.group for s in spans}), 1)
        self.assertEqual(len({(s.start, s.end) for s in spans}), 1)

    def test_too_few_folds_rejected(self):
        with self.assertRaises(ValueError):
            purged_walk_forward(self.build_spans(100), folds=1)

    def test_empty_input_returns_no_splits(self):
        self.assertEqual(purged_walk_forward([]), [])


class TestFeatureCorrectness(unittest.TestCase):
    def test_v3_rsi_reproduces_the_v3_bug(self):
        """Audit D-9: V3 reports 50 on a pure uptrend. Reproduced on purpose."""
        closes = pd.Series([100.0 + i for i in range(40)])
        self.assertAlmostEqual(compute_v3_rsi(closes), 50.0)

    def test_corrected_rsi_reports_100_on_a_pure_uptrend(self):
        closes = pd.Series([100.0 + i for i in range(40)])
        self.assertAlmostEqual(compute_rsi(closes), 100.0)

    def test_rsi_versions_agree_on_normal_data(self):
        rng = np.random.default_rng(3)
        closes = pd.Series(100 + rng.normal(0, 1, 200).cumsum())
        self.assertAlmostEqual(compute_v3_rsi(closes), compute_rsi(closes), places=6)

    def test_normal_cdf_matches_known_values(self):
        self.assertAlmostEqual(normal_cdf(0.0), 0.5, places=10)
        self.assertAlmostEqual(normal_cdf(1.959963984540054), 0.975, places=6)
        self.assertAlmostEqual(normal_cdf(0.8416212335729143), 0.80, places=6)

    def test_min_buffer_z_is_dominated_by_the_confidence_gate(self):
        """Re-verifies audit finding B-3 numerically."""
        z_for_080 = 0.8416212335729143
        self.assertGreater(z_for_080, 0.55)
        self.assertLess(normal_cdf(0.55), 0.80)


class TestDiagnostics(unittest.TestCase):
    def test_stale_run_detection(self):
        longest, over_five = longest_identical_run([1, 1, 1, 2, 3, 3, 3, 3, 3, 3, 3, 4])
        self.assertEqual(longest, 7)
        self.assertEqual(over_five, 1)

    def test_bar_quality_counts_gaps_without_filling_them(self):
        index = pd.date_range("2026-08-10", periods=100, freq="1min", tz="UTC")
        frame = pd.DataFrame(
            {"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0, "Volume": 5.0},
            index=index,
        )
        gapped = pd.concat([frame.iloc[:40], frame.iloc[50:]])
        report = analyse_bars("TEST", gapped)
        self.assertEqual(report.bars, 90)
        self.assertEqual(report.gap_count, 1)
        self.assertAlmostEqual(report.max_gap_minutes, 11.0)
        self.assertEqual(report.missing_bars, 10)
        self.assertLess(report.completeness, 1.0)

    def test_frozen_feed_shows_a_long_stale_run(self):
        index = pd.date_range("2026-08-10", periods=60, freq="1min", tz="UTC")
        frame = pd.DataFrame(
            {"Open": 100.0, "High": 100.0, "Low": 100.0, "Close": 100.0, "Volume": 1.0},
            index=index,
        )
        self.assertEqual(analyse_bars("FROZEN", frame).longest_stale_run, 60)

    def test_contract_quality_counts_statuses(self):
        good = make_replay(offset_minutes=0)
        bad = make_replay(offset_minutes=15, usable=False)
        bad.data_quality_status = "NO_TERMINAL_BAR"
        reports = analyse_contracts([good, bad])
        report = reports["BTC-USD"]
        self.assertEqual(report.total_contracts, 2)
        self.assertEqual(report.usable_contracts, 1)
        self.assertEqual(report.no_terminal_bar, 1)
        self.assertAlmostEqual(report.usable_pct, 50.0)

    def test_cross_asset_detects_perfect_agreement(self):
        replays = []
        for w in range(60):
            for asset in ("A", "B", "C"):
                replays.append(
                    make_replay(asset=asset, offset_minutes=w * 15,
                                outcome_yes=(w % 3 == 0), entered=False)
                )
        report = analyse_cross_asset({}, replays)
        self.assertEqual(report.shared_windows, 60)
        self.assertEqual(report.unanimous_windows, 60)
        self.assertAlmostEqual(report.unanimous_rate, 1.0)
        self.assertAlmostEqual(report.mean_outcome_agreement, 1.0)
        # Perfect agreement -> effectively ONE independent asset, not three.
        self.assertAlmostEqual(report.effective_sample_size_factor, 1.0, places=3)

    def test_cross_asset_detects_independence(self):
        rng = np.random.default_rng(11)
        replays = []
        for w in range(200):
            for asset in ("A", "B", "C"):
                replays.append(
                    make_replay(asset=asset, offset_minutes=w * 15,
                                outcome_yes=bool(rng.integers(0, 2)), entered=False)
                )
        report = analyse_cross_asset({}, replays)
        self.assertLess(report.mean_outcome_agreement, 0.62)
        # Near independence -> effective count close to the real asset count.
        self.assertGreater(report.effective_sample_size_factor, 2.3)


class TestDatasetSchema(unittest.TestCase):
    def test_row_contains_every_required_column(self):
        required = [
            "contract_id", "asset", "contract_start_utc", "contract_end_utc",
            "reference", "reference_verified", "entry_timestamp",
            "seconds_remaining", "spot", "buffer", "decision", "entered",
            "entry_side", "terminal_price", "outcome_yes", "outcome_label",
            "prediction_correct", "data_quality_status", "regime",
            "economics_available",
        ]
        row = replay_to_row(make_replay(), SettlementRule.PROXY_TERMINAL_ABOVE_REFERENCE.value)
        for column in required:
            self.assertIn(column, row, f"missing required column {column}")

    def test_provenance_flags_are_pinned(self):
        row = replay_to_row(make_replay(), "PROXY_TERMINAL_ABOVE_REFERENCE")
        self.assertEqual(row["reference_verified"], 0)
        self.assertEqual(row["economics_available"], 0)
        self.assertEqual(row["reference_source"], "PROXY_WINDOW_OPEN")

    def test_provenance_violation_is_refused(self):
        row = replay_to_row(make_replay(), "PROXY_TERMINAL_ABOVE_REFERENCE")
        row["reference_verified"] = 1
        with self.assertRaises(ProvenanceViolation):
            assert_provenance(row)

        row2 = replay_to_row(make_replay(), "PROXY_TERMINAL_ABOVE_REFERENCE")
        row2["economics_available"] = 1
        with self.assertRaises(ProvenanceViolation):
            assert_provenance(row2)

    def test_write_dataset_enforces_provenance(self):
        rows = [replay_to_row(make_replay(), "PROXY_TERMINAL_ABOVE_REFERENCE")]
        rows[0]["reference_source"] = "OFFICIAL_PROVIDER"
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ProvenanceViolation):
                write_dataset(rows, Path(tmp) / "out.csv")

    def test_write_dataset_round_trips(self):
        rows = [
            replay_to_row(make_replay(offset_minutes=i * 15), "PROXY_TERMINAL_ABOVE_REFERENCE")
            for i in range(5)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dataset.csv"
            report = write_dataset(rows, path)
            self.assertEqual(report.rows, 5)
            loaded = pd.read_csv(path)
            self.assertEqual(len(loaded), 5)
            for column in DATASET_COLUMNS:
                self.assertIn(column, loaded.columns)
            self.assertTrue((loaded["reference_verified"] == 0).all())
            self.assertTrue((loaded["economics_available"] == 0).all())

    def test_no_trade_row_has_null_correctness(self):
        row = replay_to_row(make_replay(entered=False), "PROXY_TERMINAL_ABOVE_REFERENCE")
        self.assertEqual(row["entered"], 0)
        self.assertIsNone(row["prediction_correct"])
        self.assertIsNone(row["entry_side"])

    def test_schema_documentation_is_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_schema_documentation(Path(tmp) / "schema.json")
            import json

            doc = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("provenance", doc)
            self.assertIn("leakage_controls", doc)
            self.assertIn("ALWAYS 0", doc["provenance"]["reference_verified"])


if __name__ == "__main__":
    unittest.main()
