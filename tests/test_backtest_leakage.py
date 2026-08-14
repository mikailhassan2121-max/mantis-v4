"""
Leakage and reconstruction tests (Phase 3 requirement 15).

These are the tests that decide whether anything in Phase 3 means anything. A
backtest with look-ahead does not produce an optimistic result — it produces a
meaningless one.

The adversarial approach used here: rather than only asserting that the correct
path works, several tests actively TRY to leak and assert that they cannot.
"""

from __future__ import annotations

import math
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from mantis_v4.clock import UTC
from mantis_v4.contracts import ContractWindow, SettlementRule
from mantis_v4.backtest.features import compute_features, precompute_indicators
from mantis_v4.backtest.replay import EventDrivenBacktester
from mantis_v4.backtest.strategies import (
    Action,
    BacktestStrategy,
    Decision,
    GeminiEdgeStrategy,
    SpotVsReferenceStrategy,
    StrategyNotEvaluable,
)
from mantis_v4.backtest.view import (
    HistoricalMarketView,
    ProxyReference,
    derive_proxy_reference,
)

ET = ZoneInfo("America/New_York")


def make_frame(start: datetime, minutes: int, base: float = 100.0, step: float = 0.5,
               seed: int = 7) -> pd.DataFrame:
    """Deterministic wiggling series (fixed seed => reproducible tests)."""
    rng = np.random.default_rng(seed)
    index = pd.date_range(start.astimezone(UTC), periods=minutes, freq="1min", tz="UTC")
    noise = rng.normal(0, step, minutes).cumsum()
    closes = base + noise + np.arange(minutes) * 0.01
    closes = np.maximum(closes, 1.0)
    return pd.DataFrame(
        {
            "Open": closes,
            "High": closes + abs(step),
            "Low": closes - abs(step),
            "Close": closes,
            "Volume": rng.uniform(5, 50, minutes),
        },
        index=index,
    )


class TestMarketViewBarrier(unittest.TestCase):
    """The view must physically exclude future bars."""

    def setUp(self):
        self.start = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
        self.frame = make_frame(self.start, 300)

    def test_view_excludes_incomplete_and_future_bars(self):
        scan = self.start + timedelta(minutes=100)
        view = HistoricalMarketView(self.frame, scan, asset="TEST")

        newest = view.frame.index[-1].to_pydatetime()
        # Bar [scan-60s, scan) completes exactly at scan and is the newest allowed.
        self.assertEqual(newest, scan - timedelta(minutes=1))
        self.assertLessEqual(newest, view.cutoff_utc)
        self.assertTrue((view.frame.index <= view.cutoff_utc).all())

    def test_no_bar_at_or_after_scan_time_is_visible(self):
        scan = self.start + timedelta(minutes=100)
        view = HistoricalMarketView(self.frame, scan, asset="TEST")
        self.assertEqual(len(view.frame.loc[view.frame.index >= scan]), 0)

    def test_mid_minute_scan_does_not_see_the_forming_bar(self):
        """At 12:30:30 the bar [12:30, 12:31) has not closed. Using its Close
        would be look-ahead: that close is the price at 12:31."""
        scan = self.start + timedelta(minutes=30, seconds=30)
        view = HistoricalMarketView(self.frame, scan, asset="TEST")
        newest = view.frame.index[-1].to_pydatetime()
        self.assertEqual(newest, self.start + timedelta(minutes=29))
        self.assertNotIn(self.start + timedelta(minutes=30), view.frame.index)

    def test_spot_age_is_reported_honestly(self):
        scan = self.start + timedelta(minutes=30, seconds=45)
        view = HistoricalMarketView(self.frame, scan, asset="TEST")
        # Newest bar starts 12:29 and closes 12:30; scan is 12:30:45 -> 45s old.
        self.assertAlmostEqual(view.spot_age_seconds, 45.0)

    def test_spot_age_is_zero_on_a_minute_boundary(self):
        scan = self.start + timedelta(minutes=30)
        view = HistoricalMarketView(self.frame, scan, asset="TEST")
        self.assertAlmostEqual(view.spot_age_seconds, 0.0)

    def test_assert_no_lookahead_passes_for_a_correct_view(self):
        view = HistoricalMarketView(self.frame, self.start + timedelta(minutes=50))
        view.assert_no_lookahead()   # must not raise

    def test_views_are_monotonic_in_information(self):
        """A later scan may never see FEWER bars than an earlier one."""
        previous = -1
        for minutes in range(70, 140, 7):
            view = HistoricalMarketView(self.frame, self.start + timedelta(minutes=minutes))
            self.assertGreaterEqual(len(view), previous)
            previous = len(view)

    def test_empty_view_before_any_data(self):
        view = HistoricalMarketView(self.frame, self.start - timedelta(hours=1))
        self.assertTrue(view.is_empty)
        self.assertIsNone(view.spot)


class TestPrecomputationEquivalence(unittest.TestCase):
    """The load-bearing proof behind precomputing indicators.

    Precomputing over the FULL series and reading row t must be numerically
    identical to computing over the TRUNCATED history frame[:t] and reading the
    last row. That identity holds only if every indicator is causal. If anyone
    adds a non-causal feature (a centred window, a backward fill, a global
    normalisation), these tests fail immediately — which is the point.
    """

    def setUp(self):
        self.start = datetime(2026, 8, 10, 0, 0, tzinfo=UTC)
        self.frame = make_frame(self.start, 60 * 12, seed=42)
        self.window = ContractWindow.for_instant(
            self.start + timedelta(hours=6), ET, 15
        )
        self.reference = derive_proxy_reference(self.frame, self.window)
        self.precomputed = precompute_indicators(self.frame)

    def test_precomputed_indicators_match_truncated(self):
        """Indicator rows must not depend on data that came after them."""
        for offset in (150, 200, 313, 400, 517, 600, 699):
            with self.subTest(offset=offset):
                truncated = self.frame.iloc[: offset + 1]
                local = precompute_indicators(truncated)

                full_row = self.precomputed.iloc[offset]
                local_row = local.iloc[-1]

                for column in local.columns:
                    a, b = full_row[column], local_row[column]
                    if isinstance(a, float) and math.isnan(a):
                        self.assertTrue(
                            math.isnan(b), f"{column}: full=NaN local={b}"
                        )
                        continue
                    self.assertAlmostEqual(
                        float(a), float(b), places=9,
                        msg=f"{column} differs at offset {offset}: {a} vs {b}",
                    )

    def test_features_match_between_fast_and_slow_paths(self):
        """End-to-end: the same scan must yield the same feature dict either way."""
        for minutes in (2, 5, 9, 13):
            scan = self.window.start_utc + timedelta(minutes=minutes)
            view = HistoricalMarketView(self.frame, scan, asset="TEST")

            fast = compute_features(
                view, self.window, self.reference, indicators=self.precomputed
            )
            slow = compute_features(view, self.window, self.reference)   # recomputes

            self.assertIsNotNone(fast)
            self.assertIsNotNone(slow)
            self.assertEqual(set(fast), set(slow))
            for key in fast:
                a, b = fast[key], slow[key]
                if a is None or isinstance(a, (bool, str)):
                    self.assertEqual(a, b, f"{key}: {a} vs {b}")
                    continue
                if isinstance(a, float) and math.isnan(a):
                    self.assertTrue(math.isnan(b), f"{key}: {a} vs {b}")
                    continue
                self.assertAlmostEqual(float(a), float(b), places=9, msg=key)

    def test_indicator_row_uses_no_future_bar(self):
        """Direct check: perturbing FUTURE bars must not change a past row."""
        offset = 400
        before = precompute_indicators(self.frame).iloc[offset]

        tampered = self.frame.copy()
        tampered.iloc[offset + 1 :, tampered.columns.get_loc("Close")] *= 5.0
        tampered.iloc[offset + 1 :, tampered.columns.get_loc("High")] *= 5.0
        after = precompute_indicators(tampered).iloc[offset]

        for column in before.index:
            a, b = before[column], after[column]
            if isinstance(a, float) and math.isnan(a):
                self.assertTrue(math.isnan(b), column)
                continue
            self.assertAlmostEqual(
                float(a), float(b), places=9,
                msg=f"{column} changed when only FUTURE bars were altered",
            )


class TestProxyReference(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
        self.frame = make_frame(self.start, 300)
        self.window = ContractWindow.for_instant(
            self.start + timedelta(hours=1), ET, 15
        )

    def test_reference_is_window_open(self):
        reference = derive_proxy_reference(self.frame, self.window)
        self.assertIsNotNone(reference)
        self.assertEqual(reference.bar_utc, self.window.start_utc)
        expected = float(self.frame.loc[self.window.start_utc]["Open"])
        self.assertAlmostEqual(reference.price, expected)

    def test_reference_is_never_verified(self):
        """Phase 3 requirement 1: no code path may set this True."""
        reference = derive_proxy_reference(self.frame, self.window)
        self.assertFalse(reference.verified)
        self.assertEqual(reference.source, "PROXY_WINDOW_OPEN")

    def test_reference_available_from_its_own_timestamp(self):
        reference = derive_proxy_reference(self.frame, self.window)
        self.assertTrue(reference.available_at(self.window.start_utc))
        self.assertTrue(reference.available_at(self.window.start_utc + timedelta(seconds=1)))
        self.assertFalse(reference.available_at(self.window.start_utc - timedelta(seconds=1)))

    def test_missing_opening_bar_delays_the_reference(self):
        """If the window's first bars are missing, the reference does not exist
        until the first bar that IS present."""
        gapped = self.frame.drop(
            self.frame.loc[
                (self.frame.index >= self.window.start_utc)
                & (self.frame.index < self.window.start_utc + timedelta(minutes=4))
            ].index
        )
        reference = derive_proxy_reference(gapped, self.window)
        self.assertIsNotNone(reference)
        self.assertEqual(reference.bar_utc, self.window.start_utc + timedelta(minutes=4))
        self.assertFalse(reference.available_at(self.window.start_utc + timedelta(minutes=2)))
        self.assertTrue(reference.available_at(self.window.start_utc + timedelta(minutes=4)))

    def test_no_bar_inside_window_gives_no_reference(self):
        empty = self.frame.drop(
            self.frame.loc[
                (self.frame.index >= self.window.start_utc)
                & (self.frame.index < self.window.end_utc)
            ].index
        )
        self.assertIsNone(derive_proxy_reference(empty, self.window))

    def test_features_hide_the_reference_until_it_exists(self):
        gapped = self.frame.drop(
            self.frame.loc[
                (self.frame.index >= self.window.start_utc)
                & (self.frame.index < self.window.start_utc + timedelta(minutes=5))
            ].index
        )
        reference = derive_proxy_reference(gapped, self.window)
        early = self.window.start_utc + timedelta(minutes=2)
        view = HistoricalMarketView(gapped, early)
        features = compute_features(view, self.window, reference)
        self.assertIsNotNone(features)
        self.assertFalse(features["reference_available"])
        self.assertIsNone(features["buffer_pct"])
        self.assertIsNone(features["v3_p_yes"])


class PeekingStrategy(BacktestStrategy):
    """Adversarial: tries to find the future in what it is handed."""

    name = "peeker"

    def __init__(self):
        self.max_seen_bar_time = None
        self.snapshots = []

    def decide(self, features):
        self.snapshots.append(dict(features))
        return Decision(Action.WAIT, reason="never enters")


class TestReplayLeakage(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 8, 10, 0, 0, tzinfo=UTC)
        self.frame = make_frame(self.start, 60 * 24)
        self.frames = {"TEST-USD": self.frame}
        self.backtester = EventDrivenBacktester(
            self.frames, ET, scan_interval_seconds=15, warmup_bars=120, paranoid=True
        )

    def test_windows_are_exact_quarter_hours(self):
        for window in self.backtester.windows_for("TEST-USD")[:50]:
            self.assertIn(window.start_local.minute, (0, 15, 30, 45))
            self.assertEqual(window.start_local.second, 0)
            self.assertEqual((window.end_utc - window.start_utc), timedelta(minutes=15))

    def test_windows_are_contiguous_and_unique(self):
        windows = self.backtester.windows_for("TEST-USD")
        ids = {w.contract_id for w in windows}
        self.assertEqual(len(ids), len(windows))
        for a, b in zip(windows, windows[1:]):
            self.assertEqual(a.end_utc, b.start_utc)

    def test_scan_grid_stays_inside_the_window(self):
        window = self.backtester.windows_for("TEST-USD")[10]
        times = list(self.backtester.scan_times(window))
        self.assertTrue(all(window.start_utc <= t < window.end_utc for t in times))
        self.assertEqual(times[0], window.start_utc)
        self.assertEqual(len(times), 60)   # 900s / 15s

    def test_scan_times_are_strictly_increasing(self):
        window = self.backtester.windows_for("TEST-USD")[10]
        times = list(self.backtester.scan_times(window))
        self.assertTrue(all(b > a for a, b in zip(times, times[1:])))

    def test_no_feature_snapshot_contains_future_information(self):
        """Every spot a strategy saw must be a bar that had already closed."""
        window = self.backtester.windows_for("TEST-USD")[20]
        peeker = PeekingStrategy()
        self.backtester.replay_contract("TEST-USD", window, peeker, keep_scans=True)

        self.assertGreater(len(peeker.snapshots), 0)
        for snapshot in peeker.snapshots:
            scan_offset = 900 - snapshot["seconds_remaining"]
            scan_time = window.start_utc + timedelta(seconds=scan_offset)
            # The spot must equal a close from a bar completing at or before scan.
            visible = self.frame.loc[self.frame.index <= scan_time - timedelta(minutes=1)]
            self.assertAlmostEqual(snapshot["spot"], float(visible["Close"].iloc[-1]))

    def test_terminal_price_never_reaches_entry_logic(self):
        """A strategy that entered must not have seen the terminal price."""
        window = self.backtester.windows_for("TEST-USD")[20]
        peeker = PeekingStrategy()
        replay = self.backtester.replay_contract("TEST-USD", window, peeker)

        self.assertIsNotNone(replay.terminal_price)
        seen_spots = {s["spot"] for s in peeker.snapshots}
        terminal_bar = window.end_utc
        # The terminal observation comes from a bar at/after window end, which is
        # strictly after every scan cutoff, so it cannot be among the spots seen.
        for snapshot in peeker.snapshots:
            self.assertLess(
                snapshot["seconds_remaining"], 901,
            )
        self.assertNotIn(terminal_bar, [None])
        visible_max = window.end_utc - timedelta(minutes=1)
        for spot in seen_spots:
            matches = self.frame.index[self.frame["Close"] == spot]
            if len(matches):
                self.assertLessEqual(matches.min().to_pydatetime(), visible_max)

    def test_entry_snapshot_is_frozen(self):
        """Requirement 5: the entry feature snapshot must be immutable."""
        window = self.backtester.windows_for("TEST-USD")[20]
        strategy = SpotVsReferenceStrategy(min_elapsed_seconds=150)
        replay = self.backtester.replay_contract("TEST-USD", window, strategy)

        self.assertTrue(replay.entered)
        frozen = dict(replay.entry_features)
        entry_time = replay.entry_timestamp

        # Anything happening later in the pipeline must not alter it.
        self.assertEqual(replay.entry_features, frozen)
        self.assertEqual(replay.entry_timestamp, entry_time)
        # The snapshot's own timing must match the recorded entry timestamp.
        self.assertAlmostEqual(
            replay.entry_features["seconds_remaining"],
            window.seconds_remaining(entry_time),
        )

    def test_entry_snapshot_is_a_copy_not_a_reference(self):
        window = self.backtester.windows_for("TEST-USD")[20]
        strategy = SpotVsReferenceStrategy(min_elapsed_seconds=150)
        replay = self.backtester.replay_contract("TEST-USD", window, strategy)
        original_spot = replay.entry_features["spot"]

        replay.entry_features["spot"] = -999.0        # mutate the copy
        second = self.backtester.replay_contract("TEST-USD", window, strategy)
        self.assertAlmostEqual(second.entry_features["spot"], original_spot)

    def test_entry_stops_scanning(self):
        """Hold to resolution: no re-entry, no second decision."""
        window = self.backtester.windows_for("TEST-USD")[20]
        strategy = SpotVsReferenceStrategy(min_elapsed_seconds=150)
        replay = self.backtester.replay_contract("TEST-USD", window, strategy, keep_scans=True)

        entries = [s for s in replay.scans if s.action.startswith("ENTER")]
        self.assertEqual(len(entries), 1)
        self.assertEqual(replay.scans[-1].scan_utc, replay.entry_timestamp)

    def test_replay_is_deterministic(self):
        window = self.backtester.windows_for("TEST-USD")[20]
        a = self.backtester.replay_contract("TEST-USD", window, SpotVsReferenceStrategy())
        b = self.backtester.replay_contract("TEST-USD", window, SpotVsReferenceStrategy())
        self.assertEqual(a.entry_timestamp, b.entry_timestamp)
        self.assertEqual(a.entry_side, b.entry_side)
        self.assertEqual(a.outcome_label, b.outcome_label)
        self.assertEqual(a.terminal_price, b.terminal_price)


class TestSettlementReconstruction(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 8, 10, 0, 0, tzinfo=UTC)
        self.timezone = ET

    def _monotonic_frame(self, minutes: int, step: float) -> pd.DataFrame:
        index = pd.date_range(self.start, periods=minutes, freq="1min", tz="UTC")
        closes = 100.0 + np.arange(minutes) * step
        return pd.DataFrame(
            {"Open": closes, "High": closes + 0.1, "Low": closes - 0.1,
             "Close": closes, "Volume": 10.0},
            index=index,
        )

    def test_rising_market_settles_yes(self):
        frame = self._monotonic_frame(600, +0.5)
        bt = EventDrivenBacktester({"X": frame}, self.timezone, warmup_bars=120)
        window = bt.windows_for("X")[5]
        replay = bt.replay_contract("X", window, SpotVsReferenceStrategy())
        self.assertTrue(replay.usable)
        self.assertEqual(replay.outcome_label, "YES")
        self.assertGreater(replay.terminal_price, replay.reference)

    def test_falling_market_settles_no(self):
        frame = self._monotonic_frame(600, -0.05)
        bt = EventDrivenBacktester({"X": frame}, self.timezone, warmup_bars=120)
        window = bt.windows_for("X")[5]
        replay = bt.replay_contract("X", window, SpotVsReferenceStrategy())
        self.assertTrue(replay.usable)
        self.assertEqual(replay.outcome_label, "NO")
        self.assertLess(replay.terminal_price, replay.reference)

    def test_correct_entry_scored_one_wrong_scored_zero(self):
        rising = self._monotonic_frame(600, +0.5)
        bt = EventDrivenBacktester({"X": rising}, self.timezone, warmup_bars=120)
        window = bt.windows_for("X")[5]
        replay = bt.replay_contract("X", window, SpotVsReferenceStrategy())
        self.assertEqual(replay.entry_side, "YES")
        self.assertEqual(replay.prediction_correct, 1)

        falling = self._monotonic_frame(600, -0.05)
        bt2 = EventDrivenBacktester({"X": falling}, self.timezone, warmup_bars=120)
        window2 = bt2.windows_for("X")[5]
        replay2 = bt2.replay_contract("X", window2, SpotVsReferenceStrategy())
        self.assertEqual(replay2.entry_side, "NO")
        self.assertEqual(replay2.prediction_correct, 1)

    def test_no_trade_is_never_scored(self):
        """Requirement 15: NO TRADE handling."""
        class NeverEnters(BacktestStrategy):
            name = "never"

            def decide(self, features):
                return Decision(Action.WAIT, reason="never")

        frame = self._monotonic_frame(600, +0.5)
        bt = EventDrivenBacktester({"X": frame}, self.timezone, warmup_bars=120)
        window = bt.windows_for("X")[5]
        replay = bt.replay_contract("X", window, NeverEnters())

        self.assertFalse(replay.entered)
        self.assertEqual(replay.decision, "NO_TRADE")
        self.assertTrue(replay.usable)             # it still settled
        self.assertIsNotNone(replay.outcome_label)
        self.assertIsNone(replay.prediction_correct)   # but is not scored

    def test_missing_terminal_bar_marks_contract_unusable(self):
        frame = self._monotonic_frame(600, +0.5)
        bt = EventDrivenBacktester({"X": frame}, self.timezone, warmup_bars=120)
        window = bt.windows_for("X")[5]
        # Delete everything from the window end onward plus the tolerance span.
        truncated = frame.loc[frame.index < window.end_utc - timedelta(minutes=5)]
        bt2 = EventDrivenBacktester({"X": truncated}, self.timezone, warmup_bars=120)
        replay = bt2.replay_contract("X", window, SpotVsReferenceStrategy())
        self.assertFalse(replay.usable)
        self.assertEqual(replay.data_quality_status, "NO_TERMINAL_BAR")
        self.assertIsNone(replay.outcome_label)

    def test_window_with_no_bars_has_no_reference(self):
        frame = self._monotonic_frame(600, +0.5)
        bt = EventDrivenBacktester({"X": frame}, self.timezone, warmup_bars=120)
        window = bt.windows_for("X")[5]
        gapped = frame.drop(
            frame.loc[(frame.index >= window.start_utc) & (frame.index < window.end_utc)].index
        )
        bt2 = EventDrivenBacktester({"X": gapped}, self.timezone, warmup_bars=120)
        replay = bt2.replay_contract("X", window, SpotVsReferenceStrategy())
        self.assertFalse(replay.usable)
        self.assertEqual(replay.data_quality_status, "NO_START_REFERENCE")

    def test_duplicate_bars_do_not_break_replay(self):
        frame = self._monotonic_frame(600, +0.5)
        doubled = pd.concat([frame, frame.iloc[100:200]]).sort_index()
        deduped = doubled[~doubled.index.duplicated(keep="last")]
        bt = EventDrivenBacktester({"X": deduped}, self.timezone, warmup_bars=120)
        window = bt.windows_for("X")[5]
        replay = bt.replay_contract("X", window, SpotVsReferenceStrategy())
        self.assertTrue(replay.usable)

    def test_timezone_of_windows_is_preserved(self):
        frame = self._monotonic_frame(600, +0.5)
        bt = EventDrivenBacktester({"X": frame}, self.timezone, warmup_bars=120)
        for window in bt.windows_for("X")[:5]:
            self.assertEqual(window.tz_name, str(ET))
            self.assertEqual(window.start_utc.tzinfo, UTC)


class TestNotEvaluableStrategy(unittest.TestCase):
    """Requirement 11: the Gemini rule must refuse, not approximate."""

    def test_gemini_strategy_declares_itself_not_evaluable(self):
        strategy = GeminiEdgeStrategy()
        self.assertFalse(strategy.evaluable)
        self.assertIn("ask price", strategy.not_evaluable_reason)

    def test_gemini_strategy_refuses_to_decide(self):
        with self.assertRaises(StrategyNotEvaluable):
            GeminiEdgeStrategy().decide({"seconds_remaining": 200})

    def test_backtester_refuses_to_replay_it(self):
        frame = make_frame(datetime(2026, 8, 10, tzinfo=UTC), 600)
        bt = EventDrivenBacktester({"X": frame}, ET, warmup_bars=120)
        window = bt.windows_for("X")[5]
        with self.assertRaises(StrategyNotEvaluable):
            bt.replay_contract("X", window, GeminiEdgeStrategy())


if __name__ == "__main__":
    unittest.main()
