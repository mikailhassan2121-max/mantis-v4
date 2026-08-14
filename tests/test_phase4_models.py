"""
Phase 4 tests (section 11).

Required coverage:
  chronological split integrity, same-window assets never split across folds,
  no feature uses future data, probability bounds [0,1], calibration fit only
  on permitted data, final holdout untouched, feature transformation fit on
  training data only, model serialization/reload, deterministic results under
  fixed seed, selective prediction metrics, clustered/window-aware evaluation.
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
from mantis_v4.contracts import ContractWindow
from mantis_v4.models.calibration import (
    IdentityCalibrator,
    IsotonicCalibrator,
    PlattCalibrator,
    assess,
    brier_score,
    calibration_intercept_slope,
    expected_calibration_error,
    log_loss,
    reliability_table,
    select_calibrator,
)
from mantis_v4.models.dataset_builder import (
    ALL_FEATURES,
    FEATURE_FAMILIES,
    ModellingDatasetBuilder,
    drop_incomplete,
    select_usable_features,
)
from mantis_v4.models.estimators import (
    LogisticModel,
    NormalZModel,
    ProbitModel,
    StandardScaler,
    load_model,
    save_model,
)
from mantis_v4.models.evaluation import (
    bucket_by_entry_time,
    clustered_accuracy,
    paired_bootstrap_difference,
    selective_prediction,
)
from mantis_v4.models.features_extended import (
    build_cross_asset_panel,
    precompute_extended,
    reference_path_features,
)
from mantis_v4.models.pipeline import (
    fit_final_model,
    run_walk_forward,
    split_train_for_calibration,
)
from mantis_v4.models.splits import (
    assert_split_integrity,
    grouped_walk_forward,
    reserve_holdout,
)

ET = ZoneInfo("America/New_York")


def make_frame(start: datetime, minutes: int, base: float = 100.0, seed: int = 5,
               drift: float = 0.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    index = pd.date_range(start.astimezone(UTC), periods=minutes, freq="1min", tz="UTC")
    steps = rng.normal(drift, base * 0.0006, minutes).cumsum()
    closes = np.maximum(base + steps, base * 0.5)
    return pd.DataFrame(
        {
            "Open": closes,
            "High": closes * 1.0004,
            "Low": closes * 0.9996,
            "Close": closes,
            "Volume": rng.uniform(10, 100, minutes),
        },
        index=index,
    )


def build_small_dataset(minutes: int = 60 * 30, assets=("AAA-USD", "BBB-USD")):
    start = datetime(2026, 7, 1, 0, 0, tzinfo=UTC)
    frames = {
        asset: make_frame(start, minutes, base=100.0 * (i + 1), seed=11 + i)
        for i, asset in enumerate(assets)
    }
    builder = ModellingDatasetBuilder(frames, ET, warmup_bars=120,
                                      scan_grid=(720, 480, 300, 120, 60))
    table, report = builder.build()
    return frames, table, report


class TestExtendedFeaturesCausality(unittest.TestCase):
    """Same mutation proof as Phase 3, applied to the new feature families."""

    def setUp(self):
        self.frame = make_frame(datetime(2026, 7, 1, tzinfo=UTC), 600, seed=3)

    def test_extended_indicators_are_causal(self):
        offset = 400
        before = precompute_extended(self.frame).iloc[offset]

        tampered = self.frame.copy()
        for column in ("Open", "High", "Low", "Close", "Volume"):
            tampered.iloc[offset + 1 :, tampered.columns.get_loc(column)] *= 7.0
        after = precompute_extended(tampered).iloc[offset]

        for column in before.index:
            a, b = before[column], after[column]
            if isinstance(a, float) and math.isnan(a):
                self.assertTrue(math.isnan(b), column)
                continue
            self.assertAlmostEqual(
                float(a), float(b), places=9,
                msg=f"{column} changed when only FUTURE bars were altered",
            )

    def test_extended_matches_truncated_history(self):
        offset = 350
        full = precompute_extended(self.frame).iloc[offset]
        local = precompute_extended(self.frame.iloc[: offset + 1]).iloc[-1]
        for column in local.index:
            a, b = full[column], local[column]
            if isinstance(a, float) and math.isnan(a):
                self.assertTrue(math.isnan(b), column)
                continue
            self.assertAlmostEqual(float(a), float(b), places=9, msg=column)

    def test_cross_asset_panel_is_causal(self):
        frames = {
            "A": make_frame(datetime(2026, 7, 1, tzinfo=UTC), 300, seed=1),
            "B": make_frame(datetime(2026, 7, 1, tzinfo=UTC), 300, seed=2),
        }
        panel = build_cross_asset_panel(frames, reference_asset="A")
        self.assertIsNotNone(panel)

        offset = 200
        before = panel.iloc[offset].copy()
        tampered = {k: v.copy() for k, v in frames.items()}
        tampered["B"].iloc[offset + 1 :, tampered["B"].columns.get_loc("Close")] *= 3.0
        after = build_cross_asset_panel(tampered, reference_asset="A").iloc[offset]

        for column in before.index:
            a, b = before[column], after[column]
            if isinstance(a, float) and math.isnan(a):
                self.assertTrue(math.isnan(b), column)
                continue
            self.assertAlmostEqual(float(a), float(b), places=9, msg=column)

    def test_reference_path_features(self):
        closes = np.array([100.0, 101.0, 99.0, 101.0, 99.0, 102.0])
        highs = closes + 0.5
        lows = closes - 0.5
        times = np.arange(6, dtype="float64") * 60.0
        out = reference_path_features(highs, lows, closes, times, 100.0, 360.0)
        self.assertEqual(out["path_bars"], 6)
        # above = [F, T, F, T, F, T]  (100 > 100 is False) -> 5 sign flips.
        self.assertEqual(out["crossings"], 5)
        self.assertGreater(out["frac_above_reference"], 0.0)
        self.assertLess(out["max_adverse_buffer"], 0.0)
        self.assertGreater(out["max_favourable_buffer"], 0.0)


class TestDatasetBuilder(unittest.TestCase):
    def setUp(self):
        self.frames, self.table, self.report = build_small_dataset()

    def test_dataset_is_built(self):
        self.assertGreater(len(self.table), 0)
        self.assertGreater(self.report.contracts_used, 0)

    def test_every_feature_column_present(self):
        for feature in ALL_FEATURES:
            self.assertIn(feature, self.table.columns, f"missing feature {feature}")

    def test_provenance_pins_carry_forward(self):
        """Phase 3 requirement 1 still holds in the Phase 4 dataset."""
        self.assertTrue((self.table.reference_verified == 0).all())
        self.assertTrue((self.table.economics_available == 0).all())
        self.assertTrue((self.table.reference_source == "PROXY_WINDOW_OPEN").all())

    def test_group_key_is_the_window_not_the_asset(self):
        """Same-window rows across assets MUST share one group."""
        sample = self.table.groupby("group_key")["asset"].nunique()
        self.assertGreater(sample.max(), 1, "expected multi-asset windows")
        for group, block in self.table.groupby("group_key"):
            self.assertEqual(block.contract_id.nunique(), 1)

    def test_labels_are_constant_within_a_window_and_asset(self):
        for (group, asset), block in self.table.groupby(["group_key", "asset"]):
            self.assertEqual(block.outcome_yes.nunique(), 1)

    def test_scan_times_are_inside_the_window(self):
        self.assertTrue((self.table.scan_utc >= self.table.window_start_utc).all())
        self.assertTrue((self.table.scan_utc < self.table.window_end_utc).all())

    def test_seconds_remaining_matches_scan_time(self):
        computed = (self.table.window_end_utc - self.table.scan_utc).dt.total_seconds()
        np.testing.assert_allclose(computed, self.table.seconds_remaining, atol=1e-6)

    def test_no_row_uses_a_future_bar(self):
        """The spot in every row must be a bar that had closed by scan time."""
        for asset, block in self.table.groupby("asset"):
            frame = self.frames[asset]
            sample = block.sample(min(40, len(block)), random_state=0)
            for _, row in sample.iterrows():
                cutoff = row.scan_utc - timedelta(minutes=1)
                visible = frame.loc[frame.index <= cutoff]
                self.assertAlmostEqual(row.spot, float(visible["Close"].iloc[-1]), places=9)

    def test_drop_incomplete_does_not_impute(self):
        table = self.table.copy()
        table.loc[table.index[:5], "rsi"] = np.nan
        cleaned, report = drop_incomplete(table, ["rsi", "spot"])
        self.assertEqual(report["dropped"], 5)
        self.assertEqual(len(cleaned), len(table) - 5)
        self.assertTrue(np.isfinite(cleaned["rsi"]).all())

    def test_structurally_missing_feature_is_dropped_not_the_rows(self):
        """Regression test for a real entry-time bias found in Phase 4.

        ``buffer_velocity`` needs six bars inside the window, so it is
        structurally undefined at the earliest scan offsets. Cleaning by
        dropping ROWS deleted 100% of the early-contract scans -- the entire
        600-900s entry-time bucket -- and shifted mean seconds_remaining from
        401 to 256. Every conclusion about entry timing would then have been
        drawn from a dataset containing no early entries.

        Correct behaviour: drop the FEATURE, keep every row.
        """
        usable, dropped = select_usable_features(self.table, ALL_FEATURES)

        self.assertIn("buffer_velocity", dropped,
                      "expected the structurally-missing feature to be dropped")
        self.assertNotIn("buffer_velocity", usable)

        cleaned, report = drop_incomplete(self.table, usable)

        # No row loss, and therefore no shift in the scan-time distribution.
        self.assertEqual(report["dropped"], 0)
        self.assertEqual(len(cleaned), len(self.table))
        self.assertAlmostEqual(
            cleaned.seconds_remaining.mean(),
            self.table.seconds_remaining.mean(),
            places=6,
        )

        # Every scan offset must survive with full population.
        for offset in self.table.seconds_remaining.unique():
            before = int((self.table.seconds_remaining == offset).sum())
            after = int((cleaned.seconds_remaining == offset).sum())
            self.assertEqual(after, before, f"scan offset {offset} lost rows")

    def test_row_dropping_alone_would_bias_entry_time(self):
        """Demonstrates the bug the previous test guards against.

        Kept deliberately: it documents WHY feature-dropping must precede
        row-dropping, so a future refactor cannot quietly reverse the order.
        """
        naive, _ = drop_incomplete(self.table, ALL_FEATURES)
        self.assertLess(
            naive.seconds_remaining.mean(),
            self.table.seconds_remaining.mean(),
            "row-dropping should demonstrably skew toward later scans",
        )
        earliest = self.table.seconds_remaining.max()
        self.assertEqual(
            int((naive.seconds_remaining == earliest).sum()), 0,
            "row-dropping should wipe out the earliest scan offset entirely",
        )

    def test_outcome_base_rate_survives_cleaning(self):
        """Cleaning must not move the label distribution."""
        usable, _ = select_usable_features(self.table, ALL_FEATURES)
        cleaned, _ = drop_incomplete(self.table, usable)
        self.assertAlmostEqual(
            cleaned.outcome_yes.mean(), self.table.outcome_yes.mean(), places=6
        )


class TestSplits(unittest.TestCase):
    def setUp(self):
        _, self.table, _ = build_small_dataset()
        self.plan = reserve_holdout(self.table, holdout_fraction=0.25)

    def test_holdout_is_chronologically_last(self):
        self.assertLess(self.plan.dev.window_epoch.max(),
                        self.plan.holdout.window_epoch.min())

    def test_holdout_and_dev_share_no_window(self):
        overlap = set(self.plan.dev.group_key) & set(self.plan.holdout.group_key)
        self.assertEqual(overlap, set())

    def test_final_holdout_untouched(self):
        """No fold in the development set may contain a holdout window."""
        splits = grouped_walk_forward(self.plan.dev, folds=4)
        self.assertGreater(len(splits), 0)
        for split in splits:
            train_groups = set(self.plan.dev.iloc[split.train_idx].group_key)
            test_groups = set(self.plan.dev.iloc[split.test_idx].group_key)
            self.plan.seal.assert_disjoint(train_groups, f"fold{split.fold}-train")
            self.plan.seal.assert_disjoint(test_groups, f"fold{split.fold}-test")
        self.assertTrue(self.plan.seal.intact)

    def test_seal_detects_a_violation(self):
        contaminated = set(self.plan.holdout.group_key)
        with self.assertRaises(AssertionError):
            self.plan.seal.assert_disjoint(contaminated, "deliberate violation")
        self.assertFalse(self.plan.seal.intact)

    def test_splits_are_chronological(self):
        for split in grouped_walk_forward(self.plan.dev, folds=4):
            assert_split_integrity(self.plan.dev, split)

    def test_same_window_assets_never_split(self):
        """The central grouping guarantee."""
        for split in grouped_walk_forward(self.plan.dev, folds=4):
            train_groups = set(self.plan.dev.iloc[split.train_idx].group_key)
            test_groups = set(self.plan.dev.iloc[split.test_idx].group_key)
            self.assertEqual(train_groups & test_groups, set())

    def test_no_row_appears_in_both_train_and_test(self):
        for split in grouped_walk_forward(self.plan.dev, folds=4):
            self.assertEqual(set(split.train_idx) & set(split.test_idx), set())

    def test_train_precedes_test_in_time(self):
        for split in grouped_walk_forward(self.plan.dev, folds=4):
            train_max = self.plan.dev.iloc[split.train_idx].window_epoch.max()
            test_min = self.plan.dev.iloc[split.test_idx].window_epoch.min()
            self.assertLess(train_max, test_min)

    def test_calibration_split_is_grouped_and_chronological(self):
        splits = grouped_walk_forward(self.plan.dev, folds=4)
        fit_idx, cal_idx = split_train_for_calibration(self.plan.dev, splits[-1].train_idx)
        self.assertGreater(len(fit_idx), 0)
        self.assertGreater(len(cal_idx), 0)
        fit_groups = set(self.plan.dev.iloc[fit_idx].group_key)
        cal_groups = set(self.plan.dev.iloc[cal_idx].group_key)
        self.assertEqual(fit_groups & cal_groups, set())
        self.assertLess(self.plan.dev.iloc[fit_idx].window_epoch.max(),
                        self.plan.dev.iloc[cal_idx].window_epoch.min())

    def test_bad_fold_count_rejected(self):
        with self.assertRaises(ValueError):
            grouped_walk_forward(self.plan.dev, folds=1)

    def test_bad_holdout_fraction_rejected(self):
        with self.assertRaises(ValueError):
            reserve_holdout(self.table, holdout_fraction=0.0)
        with self.assertRaises(ValueError):
            reserve_holdout(self.table, holdout_fraction=1.0)


class TestEstimators(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(0)
        self.n = 800
        self.X = rng.normal(0, 1, (self.n, 4))
        logit = 1.2 * self.X[:, 0] - 0.8 * self.X[:, 1]
        self.y = (rng.uniform(0, 1, self.n) < 1 / (1 + np.exp(-logit))).astype(int)
        self.names = ["normal_z", "f2", "f3", "f4"]

    def test_probabilities_are_in_bounds(self):
        for model in (
            NormalZModel(),
            LogisticModel("logit", penalty=None),
            LogisticModel("l2", penalty="l2"),
            ProbitModel("probit"),
        ):
            model.fit(self.X, self.y, self.names)
            p = model.predict_proba(self.X)
            self.assertTrue(np.all(p >= 0.0), model.name)
            self.assertTrue(np.all(p <= 1.0), model.name)
            self.assertTrue(np.all(np.isfinite(p)), model.name)

    def test_normal_z_fits_nothing(self):
        model = NormalZModel()
        self.assertFalse(model.requires_fit)
        model.fit(self.X, self.y, self.names)
        expected_high = model.predict_proba(np.array([[2.0, 0, 0, 0]]))[0]
        expected_low = model.predict_proba(np.array([[-2.0, 0, 0, 0]]))[0]
        self.assertGreater(expected_high, 0.97)
        self.assertLess(expected_low, 0.03)

    def test_normal_z_requires_its_column(self):
        with self.assertRaises(ValueError):
            NormalZModel().fit(self.X, self.y, ["a", "b", "c", "d"])

    def test_probit_and_logit_broadly_agree(self):
        logit = LogisticModel("logit", penalty=None).fit(self.X, self.y, self.names)
        probit = ProbitModel("probit").fit(self.X, self.y, self.names)
        p1 = logit.predict_proba(self.X)
        p2 = probit.predict_proba(self.X)
        self.assertLess(float(np.mean(np.abs(p1 - p2))), 0.05)

    def test_scaler_fit_on_train_only(self):
        """The transform must not see rows outside the training block."""
        train, test = self.X[:400], self.X[400:] * 50.0 + 1000.0
        scaler = StandardScaler().fit(train)
        np.testing.assert_allclose(scaler.mean_, train.mean(axis=0))
        # Re-fitting on the full data would move the mean; it must not have.
        full_mean = np.vstack([train, test]).mean(axis=0)
        self.assertFalse(np.allclose(scaler.mean_, full_mean))

    def test_scaler_handles_constant_column(self):
        X = np.column_stack([np.ones(50), np.arange(50, dtype="float64")])
        scaled = StandardScaler().fit(X).transform(X)
        self.assertTrue(np.all(np.isfinite(scaled)))

    def test_model_serialization_and_reload(self):
        model = LogisticModel("logit", penalty="l2").fit(self.X, self.y, self.names)
        original = model.predict_proba(self.X)
        with tempfile.TemporaryDirectory() as tmp:
            path = save_model(model, Path(tmp) / "m.pkl")
            reloaded = load_model(path)
        np.testing.assert_allclose(original, reloaded.predict_proba(self.X))

    def test_coefficients_are_reported(self):
        model = LogisticModel("logit", penalty=None).fit(self.X, self.y, self.names)
        coefs = model.coefficients()
        self.assertIn("(intercept)", coefs)
        for name in self.names:
            self.assertIn(name, coefs)
        self.assertEqual(len(model.top_coefficients(2)), 2)

    def test_deterministic_under_fixed_seed(self):
        a = LogisticModel("x", penalty="l2", seed=7).fit(self.X, self.y, self.names)
        b = LogisticModel("x", penalty="l2", seed=7).fit(self.X, self.y, self.names)
        np.testing.assert_array_equal(a.predict_proba(self.X), b.predict_proba(self.X))


class TestCalibration(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(4)
        self.n = 6000

        # Build a GENUINELY overconfident forecaster, which is subtler than it
        # first looks. Deriving the forecast from the realised outcome produces
        # an ORACLE, not an overconfident model -- an oracle fits a calibration
        # slope ABOVE 1 because its probabilities are not extreme enough for how
        # good it actually is.
        #
        # True overconfidence means the forecast is more extreme than the TRUE
        # generating probability. So: draw a true probability, draw the outcome
        # from it, then report a logit-sharpened version of the truth.
        true_p = rng.uniform(0.25, 0.75, self.n)
        self.y = (rng.uniform(0, 1, self.n) < true_p).astype(int)

        true_logit = np.log(true_p / (1 - true_p))
        self.p_good = 1.0 / (1.0 + np.exp(-true_logit))            # honest
        self.p_over = 1.0 / (1.0 + np.exp(-2.2 * true_logit))      # too extreme
        self.p_under = 1.0 / (1.0 + np.exp(-0.4 * true_logit))     # too timid

    def test_perfect_forecast_scores_zero(self):
        y = np.array([1, 0, 1, 0])
        self.assertAlmostEqual(brier_score(y.astype(float), y), 0.0)

    def test_coin_flip_brier_is_quarter(self):
        self.assertAlmostEqual(brier_score(np.full(4, 0.5), np.array([1, 0, 1, 0])), 0.25)

    def test_calibration_slope_detects_overconfidence(self):
        """slope < 1 == too extreme; slope > 1 == too timid; 1 == honest."""
        _, slope_over = calibration_intercept_slope(self.p_over, self.y)
        _, slope_good = calibration_intercept_slope(self.p_good, self.y)
        _, slope_under = calibration_intercept_slope(self.p_under, self.y)

        self.assertLess(slope_over, 1.0, "overconfident forecaster should fit slope < 1")
        self.assertGreater(slope_under, 1.0, "timid forecaster should fit slope > 1")
        self.assertLess(slope_over, slope_good)
        self.assertGreater(slope_under, slope_good)
        self.assertAlmostEqual(slope_good, 1.0, delta=0.25)

    def test_assess_reports_a_verdict(self):
        self.assertIn("OVERCONFIDENT", assess(self.p_over, self.y, "over").verdict)
        self.assertIn("UNDERCONFIDENT", assess(self.p_under, self.y, "under").verdict)
        self.assertGreater(assess(self.p_over, self.y, "over").ece, 0.0)

    def test_reliability_buckets_fold_to_confidence(self):
        p = np.array([0.9, 0.1, 0.85, 0.15])
        y = np.array([1, 0, 1, 0])          # every prediction correct
        stats = reliability_table(p, y)
        populated = [s for s in stats if s.count > 0]
        self.assertTrue(all(s.observed_rate == 1.0 for s in populated))
        # 0.1 should be folded into the 0.90-0.95 bucket, not left at 0.10.
        self.assertTrue(any(s.low >= 0.85 for s in populated))

    def test_ece_is_zero_for_perfect_calibration(self):
        p = np.full(2000, 0.75)
        rng = np.random.default_rng(9)
        y = (rng.uniform(0, 1, 2000) < 0.75).astype(int)
        stats = reliability_table(p, y)
        ece, _ = expected_calibration_error(stats)
        self.assertLess(ece, 0.05)

    def test_calibrators_preserve_bounds(self):
        for calibrator in (IdentityCalibrator(), PlattCalibrator(), IsotonicCalibrator()):
            calibrator.fit(self.p_over, self.y)
            out = calibrator.transform(self.p_over)
            self.assertTrue(np.all(out >= 0.0) and np.all(out <= 1.0), calibrator.name)

    def test_platt_improves_an_overconfident_forecaster(self):
        half = self.n // 2
        calibrator = PlattCalibrator().fit(self.p_over[:half], self.y[:half])
        after = calibrator.transform(self.p_over[half:])
        self.assertLess(log_loss(after, self.y[half:]),
                        log_loss(self.p_over[half:], self.y[half:]))

    def test_calibration_selected_on_separate_data(self):
        """Fit rows and selection rows are distinct arguments by design."""
        half = self.n // 2
        best, scores = select_calibrator(
            self.p_over[:half], self.y[:half],
            self.p_over[half:], self.y[half:],
        )
        self.assertIn(best.name, {"uncalibrated", "platt", "isotonic"})
        self.assertEqual(set(scores), {"uncalibrated", "platt", "isotonic"})
        self.assertEqual(scores[best.name], min(scores.values()))


class TestEvaluation(unittest.TestCase):
    def test_clustered_interval_is_wider_than_naive(self):
        """The whole point of clustering: honest intervals are wider."""
        rng = np.random.default_rng(6)
        groups, correct = [], []
        for g in range(60):
            outcome = rng.uniform() < 0.7
            for _ in range(16):            # 16 correlated rows per window
                groups.append(f"w{g}")
                correct.append(1.0 if outcome else 0.0)
        interval = clustered_accuracy(np.array(correct), np.array(groups), n_boot=400)
        self.assertGreater(interval.width, interval.naive_width)
        self.assertGreater(interval.inflation, 1.5)

    def test_selective_prediction_coverage_falls_as_threshold_rises(self):
        rng = np.random.default_rng(8)
        n = 3000
        y = rng.integers(0, 2, n)
        p = np.clip(np.where(y == 1, 0.7, 0.3) + rng.normal(0, 0.15, n), 0.01, 0.99)
        groups = np.array([f"w{i//10}" for i in range(n)])
        rows = selective_prediction(p, y, groups, n_boot=100)
        coverages = [r.coverage for r in rows]
        self.assertEqual(coverages, sorted(coverages, reverse=True))

    def test_selective_prediction_accuracy_rises_with_threshold(self):
        rng = np.random.default_rng(12)
        n = 4000
        y = rng.integers(0, 2, n)
        p = np.clip(np.where(y == 1, 0.68, 0.32) + rng.normal(0, 0.18, n), 0.01, 0.99)
        groups = np.array([f"w{i//10}" for i in range(n)])
        rows = [r for r in selective_prediction(p, y, groups, n_boot=100) if r.n_trades > 50]
        self.assertGreater(rows[-1].accuracy, rows[0].accuracy)

    def test_selective_row_has_no_economic_field(self):
        rng = np.random.default_rng(1)
        n = 500
        y = rng.integers(0, 2, n)
        p = rng.uniform(0.3, 0.7, n)
        groups = np.array([f"w{i//10}" for i in range(n)])
        row = selective_prediction(p, y, groups, thresholds=(0.55,), n_boot=50)[0].as_row()
        self.assertIn("NOT EVALUABLE", row["economic_profitability"])

    def test_entry_time_bucketing(self):
        seconds = np.array([870, 700, 500, 400, 200, 100, 45, 10])
        labels = list(bucket_by_entry_time(seconds))
        self.assertEqual(labels[0], "600-900s")
        self.assertEqual(labels[2], "450-600s")
        self.assertEqual(labels[-1], "0-30s")

    def test_paired_bootstrap_detects_a_better_model(self):
        rng = np.random.default_rng(15)
        n = 2000
        y = rng.integers(0, 2, n)
        good = np.clip(np.where(y == 1, 0.8, 0.2) + rng.normal(0, 0.05, n), 0.01, 0.99)
        bad = np.full(n, 0.5)
        groups = np.array([f"w{i//10}" for i in range(n)])
        point, low, high = paired_bootstrap_difference(good, bad, y, groups, "brier", n_boot=200)
        self.assertLess(point, 0.0)      # negative => `good` has lower Brier
        self.assertLess(high, 0.0)       # and the interval excludes zero


class TestPipelineIntegration(unittest.TestCase):
    def setUp(self):
        _, self.table, _ = build_small_dataset(minutes=60 * 40)
        cleaned, _ = drop_incomplete(self.table, ALL_FEATURES)
        self.table = cleaned
        self.plan = reserve_holdout(self.table, holdout_fraction=0.25)
        self.features = FEATURE_FAMILIES["geometry"]

    def test_walk_forward_produces_out_of_sample_predictions(self):
        result = run_walk_forward(
            self.plan.dev, self.features,
            lambda: LogisticModel("test", penalty="l2", seed=1),
            folds=4, feature_set="geometry",
        )
        self.assertGreater(len(result.folds), 0)
        self.assertGreater(result.n, 0)
        self.assertTrue(np.all(result.p_calibrated >= 0.0))
        self.assertTrue(np.all(result.p_calibrated <= 1.0))

    def test_walk_forward_predictions_are_deterministic(self):
        def build():
            return run_walk_forward(
                self.plan.dev, self.features,
                lambda: LogisticModel("test", penalty="l2", seed=3),
                folds=4,
            )
        np.testing.assert_array_equal(build().p_calibrated, build().p_calibrated)

    def test_walk_forward_never_predicts_a_training_row(self):
        result = run_walk_forward(
            self.plan.dev, self.features,
            lambda: LogisticModel("test", penalty="l2", seed=1), folds=4,
        )
        seen = set()
        for fold in result.folds:
            self.assertEqual(seen & set(fold.test_idx), set())
            seen |= set(fold.test_idx)

    def test_normal_z_runs_without_fitting(self):
        result = run_walk_forward(
            self.plan.dev, self.features, NormalZModel, folds=4, model_name="normal_z"
        )
        self.assertGreater(result.n, 0)
        self.assertTrue(np.all(np.isfinite(result.p_raw)))

    def test_final_model_fit_and_holdout_stay_disjoint(self):
        model, calibrator, meta = fit_final_model(
            self.plan.dev, self.features,
            lambda: LogisticModel("final", penalty="l2", seed=2),
        )
        p = calibrator.transform(
            model.predict_proba(self.plan.holdout[self.features].to_numpy(dtype="float64"))
        )
        self.assertEqual(len(p), len(self.plan.holdout))
        self.assertTrue(np.all(p >= 0.0) and np.all(p <= 1.0))
        self.plan.seal.assert_disjoint(set(self.plan.dev.group_key), "final fit data")
        self.assertTrue(self.plan.seal.intact)


if __name__ == "__main__":
    unittest.main()
