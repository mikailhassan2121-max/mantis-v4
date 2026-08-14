"""
Data-quality gate tests (master prompt section 18).

Every check must FAIL CLOSED. Audit D-6 found V3's data-age function returned
0.0 -- maximum freshness -- on any unexpected exception, which is the exact
inversion of what a data-integrity check should do.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from mantis_v4.clock import UTC, Instant
from mantis_v4.contracts import (
    ContractQuote,
    ContractSpec,
    ContractWindow,
    ReferenceSource,
    SettlementRule,
)
from mantis_v4.providers.base import BarSet
from mantis_v4.providers.market_data import (
    bar_gap_report,
    contiguous_log_returns,
    distinct_close_count,
    normalize_bars,
)
from mantis_v4.quality import QualityThresholds, evaluate_data_quality

ET = ZoneInfo("America/New_York")
THRESHOLDS = QualityThresholds()

_DEFAULT_SPEC = object()   # sentinel so tests can pass spec=None deliberately


def build_frame(start: datetime, minutes: int, closes) -> pd.DataFrame:
    index = pd.date_range(start.astimezone(UTC), periods=minutes, freq="1min", tz="UTC")
    closes = list(closes)
    return pd.DataFrame(
        {
            "Open": closes,
            "High": [c + 1 for c in closes],
            "Low": [c - 1 for c in closes],
            "Close": closes,
            "Volume": [10.0] * minutes,
        },
        index=index,
    )


def moving_closes(n: int, base: float = 100.0) -> list[float]:
    """Deterministic wiggling series with plenty of distinct values."""
    return [base + (i % 7) * 0.5 + i * 0.01 for i in range(n)]


def make_barset(frame: pd.DataFrame, partial=False, cached=False) -> BarSet:
    return BarSet(
        asset="BTC-USD",
        frame=frame,
        source="test",
        fetched_at_utc=frame.index[-1].to_pydatetime(),
        last_bar_is_partial=partial,
        from_cache=cached,
    )


class QualityTestCase(unittest.TestCase):
    def setUp(self):
        self.window = ContractWindow.for_instant(
            datetime(2026, 8, 13, 15, 0, tzinfo=UTC), ET, 15
        )
        self.now = Instant(utc=datetime(2026, 8, 13, 15, 5, 0, tzinfo=UTC), monotonic=0.0)
        self.start = datetime(2026, 8, 13, 13, 30, tzinfo=UTC)
        self.spec = ContractSpec(
            window=self.window,
            underlying="BTC-USD",
            reference_price=100.0,
            reference_source=ReferenceSource.PROXY_WINDOW_OPEN,
            settlement_rule=SettlementRule.PROXY_TERMINAL_ABOVE_REFERENCE,
        )

    def evaluate(self, bars, spec=_DEFAULT_SPEC, **kwargs):
        # Sentinel, not None: a test must be able to pass spec=None explicitly
        # to exercise the missing-reference path.
        return evaluate_data_quality(
            asset="BTC-USD",
            bars=bars,
            window=self.window,
            instant=self.now,
            spec=self.spec if spec is _DEFAULT_SPEC else spec,
            thresholds=THRESHOLDS,
            **kwargs,
        )

    def good_bars(self) -> BarSet:
        # 95 bars ending at 15:04 -> last bar is 1 minute old at 15:05.
        frame = build_frame(self.start, 95, moving_closes(95))
        return make_barset(frame)

    def failure_names(self, report) -> set:
        return {check.name for check in report.failures}


class TestHappyPath(QualityTestCase):
    def test_good_data_passes(self):
        report = self.evaluate(self.good_bars())
        self.assertTrue(report.passed, f"unexpected failures: {report.failures}")
        self.assertEqual(report.summary[:2], "OK")


class TestFailClosed(QualityTestCase):
    def test_no_bars_fails(self):
        report = self.evaluate(None)
        self.assertFalse(report.passed)
        self.assertIn("bars_present", self.failure_names(report))

    def test_empty_report_is_not_a_pass(self):
        """An absence of checks must never read as success."""
        from mantis_v4.quality import DataQualityReport

        self.assertFalse(DataQualityReport(asset="BTC-USD").passed)

    def test_insufficient_history_fails(self):
        frame = build_frame(self.start, 10, moving_closes(10))
        report = self.evaluate(make_barset(frame))
        self.assertFalse(report.passed)
        self.assertIn("sufficient_history", self.failure_names(report))

    def test_stale_data_fails(self):
        """Audit D-2: V3 allowed a 5-minute-old price to drive a 15-minute contract."""
        old_start = self.start - timedelta(hours=3)
        frame = build_frame(old_start, 95, moving_closes(95))
        report = self.evaluate(make_barset(frame))
        self.assertFalse(report.passed)
        self.assertIn("data_age", self.failure_names(report))

    def test_future_timestamp_fails(self):
        future = self.now.utc + timedelta(minutes=30)
        frame = build_frame(future, 95, moving_closes(95))
        report = self.evaluate(make_barset(frame))
        self.assertFalse(report.passed)
        self.assertIn("data_age", self.failure_names(report))

    def test_frozen_feed_fails(self):
        """Audit B-5: a frozen feed collapses volatility and manufactured
        96.8% confidence in V3. Timestamp freshness cannot detect it."""
        frame = build_frame(self.start, 95, [100.0] * 95)
        report = self.evaluate(make_barset(frame))
        self.assertFalse(report.passed)
        names = self.failure_names(report)
        self.assertIn("price_movement", names)
        self.assertIn("volatility_estimable", names)

    def test_nearly_frozen_feed_fails(self):
        closes = [100.0] * 92 + [100.0, 100.0, 100.05]
        frame = build_frame(self.start, 95, closes)
        report = self.evaluate(make_barset(frame))
        self.assertFalse(report.passed)
        self.assertIn("price_movement", self.failure_names(report))

    def test_nan_values_fail(self):
        closes = moving_closes(95)
        frame = build_frame(self.start, 95, closes)
        frame.iloc[-2, frame.columns.get_loc("Close")] = np.nan
        report = self.evaluate(make_barset(frame))
        self.assertFalse(report.passed)
        self.assertIn("finite_values", self.failure_names(report))

    def test_inf_values_fail(self):
        frame = build_frame(self.start, 95, moving_closes(95))
        frame.iloc[-1, frame.columns.get_loc("High")] = np.inf
        report = self.evaluate(make_barset(frame))
        self.assertFalse(report.passed)
        self.assertIn("finite_values", self.failure_names(report))

    def test_non_positive_price_fails(self):
        frame = build_frame(self.start, 95, moving_closes(95))
        frame.iloc[-1, frame.columns.get_loc("Low")] = -5.0
        report = self.evaluate(make_barset(frame))
        self.assertFalse(report.passed)
        self.assertIn("finite_values", self.failure_names(report))

    def test_out_of_order_bars_fail(self):
        frame = build_frame(self.start, 95, moving_closes(95))
        frame = frame.iloc[::-1]
        report = self.evaluate(make_barset(frame))
        self.assertFalse(report.passed)
        self.assertIn("bar_ordering", self.failure_names(report))

    def test_tz_naive_index_fails(self):
        frame = build_frame(self.start, 95, moving_closes(95))
        frame.index = frame.index.tz_localize(None)
        report = self.evaluate(make_barset(frame))
        self.assertFalse(report.passed)
        self.assertIn("timezone_aware", self.failure_names(report))

    def test_missing_reference_fails(self):
        report = self.evaluate(self.good_bars(), spec=None)
        self.assertFalse(report.passed)
        self.assertIn("reference_known", self.failure_names(report))

    def test_rollover_race_fails(self):
        """Audit A-4: the scan instant must belong to the window being judged."""
        other = ContractWindow.for_instant(
            datetime(2026, 8, 13, 18, 0, tzinfo=UTC), ET, 15
        )
        report = evaluate_data_quality(
            asset="BTC-USD",
            bars=self.good_bars(),
            window=other,
            instant=self.now,
            spec=self.spec,
            thresholds=THRESHOLDS,
        )
        self.assertFalse(report.passed)
        self.assertIn("contract_window_current", self.failure_names(report))


class TestWarnings(QualityTestCase):
    def test_proxy_reference_warns_but_does_not_block_research(self):
        report = self.evaluate(self.good_bars())
        self.assertTrue(report.passed)
        warnings = {c.name for c in report.warnings}
        self.assertIn("reference_known", warnings)

    def test_partial_bar_warns(self):
        frame = build_frame(self.start, 95, moving_closes(95))
        report = self.evaluate(make_barset(frame, partial=True))
        self.assertTrue(report.passed)
        self.assertIn("partial_bar", {c.name for c in report.warnings})

    def test_cached_feed_warns(self):
        frame = build_frame(self.start, 95, moving_closes(95))
        report = self.evaluate(make_barset(frame, cached=True))
        self.assertTrue(report.passed)
        self.assertIn("live_feed", {c.name for c in report.warnings})


class TestEconomicsGate(QualityTestCase):
    def verified_spec(self, quote: ContractQuote) -> ContractSpec:
        return ContractSpec(
            window=self.window,
            underlying="BTC-USD",
            reference_price=100.0,
            reference_source=ReferenceSource.OFFICIAL_PROVIDER,
            settlement_rule=SettlementRule.TERMINAL_ABOVE_REFERENCE,
            quote=quote,
        )

    def test_fresh_quote_passes(self):
        quote = ContractQuote(
            yes_bid=0.54, yes_ask=0.55, no_bid=0.44, no_ask=0.46,
            quote_timestamp=self.now.utc - timedelta(seconds=3),
        )
        report = self.evaluate(
            self.good_bars(), spec=self.verified_spec(quote), require_economics=True
        )
        self.assertTrue(report.passed, f"unexpected: {report.failures}")

    def test_stale_quote_blocks_ev_mode(self):
        quote = ContractQuote(
            yes_bid=0.54, yes_ask=0.55, no_bid=0.44, no_ask=0.46,
            quote_timestamp=self.now.utc - timedelta(seconds=120),
        )
        report = self.evaluate(
            self.good_bars(), spec=self.verified_spec(quote), require_economics=True
        )
        self.assertFalse(report.passed)
        self.assertIn("quote_fresh", self.failure_names(report))

    def test_one_sided_quote_blocks_ev_mode(self):
        quote = ContractQuote(yes_ask=0.55, quote_timestamp=self.now.utc)
        report = self.evaluate(
            self.good_bars(), spec=self.verified_spec(quote), require_economics=True
        )
        self.assertFalse(report.passed)
        self.assertIn("quote_fresh", self.failure_names(report))

    def test_crossed_quote_blocks_ev_mode(self):
        quote = ContractQuote(
            yes_bid=0.70, yes_ask=0.55, no_bid=0.44, no_ask=0.46,
            quote_timestamp=self.now.utc,
        )
        report = self.evaluate(
            self.good_bars(), spec=self.verified_spec(quote), require_economics=True
        )
        self.assertFalse(report.passed)
        self.assertIn("quote_fresh", self.failure_names(report))


class TestCrossProvider(QualityTestCase):
    def test_agreeing_providers_pass(self):
        bars = self.good_bars()
        report = self.evaluate(bars, cross_provider_price=bars.last_close)
        self.assertTrue(report.passed)

    def test_disagreeing_providers_fail(self):
        bars = self.good_bars()
        report = self.evaluate(bars, cross_provider_price=bars.last_close * 1.10)
        self.assertFalse(report.passed)
        self.assertIn("cross_provider", self.failure_names(report))


class TestBarHelpers(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 8, 13, 13, 0, tzinfo=UTC)

    def test_contiguous_returns_skip_gaps(self):
        """Audit D-5: a gap-spanning return must not count as a 1-minute return."""
        frame = build_frame(self.start, 20, moving_closes(20))
        # Remove a block of bars to create a 5-minute hole.
        frame = pd.concat([frame.iloc[:10], frame.iloc[15:]])

        naive = np.log(frame["Close"] / frame["Close"].shift(1)).dropna()
        contiguous = contiguous_log_returns(frame)

        self.assertEqual(len(naive), len(frame) - 1)
        self.assertEqual(len(contiguous), len(frame) - 2)   # the gap return is dropped

    def test_gap_report(self):
        frame = build_frame(self.start, 20, moving_closes(20))
        frame = pd.concat([frame.iloc[:10], frame.iloc[15:]])
        report = bar_gap_report(frame)
        self.assertEqual(report["gaps"], 1)
        # Bars 10-14 are removed, so bar 9 (t+9m) is followed by bar 15 (t+15m):
        # the observed inter-bar delta is 6 minutes, not the 5 bars dropped.
        self.assertAlmostEqual(report["max_gap_minutes"], 6.0)

    def test_distinct_close_count(self):
        flat = build_frame(self.start, 30, [100.0] * 30)
        self.assertEqual(distinct_close_count(flat, 20), 1)
        varied = build_frame(self.start, 30, moving_closes(30))
        self.assertGreater(distinct_close_count(varied, 20), 5)

    def test_normalize_rejects_missing_columns(self):
        frame = pd.DataFrame({"Close": [1, 2, 3]})
        self.assertIsNone(normalize_bars(frame))

    def test_normalize_localizes_naive_index_to_utc(self):
        frame = build_frame(self.start, 5, moving_closes(5))
        frame.index = frame.index.tz_localize(None)
        result = normalize_bars(frame)
        self.assertIsNotNone(result)
        self.assertEqual(str(result.index.tz), "UTC")

    def test_normalize_dedupes_and_sorts(self):
        frame = build_frame(self.start, 5, moving_closes(5))
        doubled = pd.concat([frame, frame]).sort_index(ascending=False)
        result = normalize_bars(doubled)
        self.assertEqual(len(result), 5)
        self.assertTrue(result.index.is_monotonic_increasing)

    def test_normalize_drops_nan_rows(self):
        frame = build_frame(self.start, 5, moving_closes(5))
        frame.iloc[2, frame.columns.get_loc("Close")] = np.nan
        result = normalize_bars(frame)
        self.assertEqual(len(result), 4)


if __name__ == "__main__":
    unittest.main()
