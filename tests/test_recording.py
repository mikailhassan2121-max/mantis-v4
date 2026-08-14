"""
Recording and resolution tests.

These are the tests that matter most in Phase 2, because they enforce the
standing instruction:

    Do not allow any version of MANTIS to resolve a contract without logging
    its outcome.

Audit findings under test: A-2 (rollover wrote nothing), C-1 (no outcome labels
existed at all), C-2 (NO TRADE decisions were never recorded), C-4 (no state
survived a restart).
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from mantis_v4.clock import UTC, Instant
from mantis_v4.contracts import (
    ContractSpec,
    ContractStatus,
    ContractWindow,
    ReferenceSource,
    SettlementRule,
)
from mantis_v4.providers.base import BarSet
from mantis_v4.recording import ContractRecorder, PredictionRecord
from mantis_v4.resolution import ResolutionEngine, find_terminal_price

ET = ZoneInfo("America/New_York")


def instant_at(moment: datetime) -> Instant:
    return Instant(utc=moment.astimezone(UTC), monotonic=0.0)


def make_bars(asset: str, start: datetime, minutes: int, price: float, step: float = 1.0) -> BarSet:
    """Bars with a strictly increasing price so outcomes are unambiguous."""
    index = pd.date_range(start.astimezone(UTC), periods=minutes, freq="1min", tz="UTC")
    prices = [price + step * i for i in range(minutes)]
    frame = pd.DataFrame(
        {
            "Open": prices,
            "High": [p + 0.5 for p in prices],
            "Low": [p - 0.5 for p in prices],
            "Close": [p + 0.25 for p in prices],
            "Volume": [100.0] * minutes,
        },
        index=index,
    )
    return BarSet(
        asset=asset,
        frame=frame,
        source="test",
        fetched_at_utc=index[-1].to_pydatetime(),
        last_bar_is_partial=False,
    )


def make_spec(window: ContractWindow, asset="BTC-USD", reference=100.0,
              source=ReferenceSource.PROXY_WINDOW_OPEN,
              rule=SettlementRule.PROXY_TERMINAL_ABOVE_REFERENCE) -> ContractSpec:
    return ContractSpec(
        window=window,
        underlying=asset,
        reference_price=reference,
        reference_source=source,
        settlement_rule=rule,
        provider_name="test",
    )


class RecorderTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.recorder = ContractRecorder(
            base / "test.db",
            contracts_csv=base / "contracts.csv",
            predictions_csv=base / "predictions.csv",
            model_version="test",
        )
        self.window = ContractWindow.for_instant(
            datetime(2026, 8, 13, 15, 0, tzinfo=UTC), ET, 15
        )

    def tearDown(self):
        self.recorder.close()
        self._tmp.cleanup()


class TestDatabaseWrites(RecorderTestCase):
    def test_observe_creates_open_contract(self):
        spec = make_spec(self.window)
        key = self.recorder.observe_contract(spec, instant_at(self.window.start_utc), spot=100.0)

        row = self.recorder.get_contract(key)
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], ContractStatus.OPEN.value)
        self.assertEqual(row["asset"], "BTC-USD")
        self.assertEqual(row["reference_price"], 100.0)
        self.assertEqual(row["reference_verified"], 0)
        self.assertEqual(row["scan_count"], 1)

    def test_repeated_observation_updates_not_duplicates(self):
        spec = make_spec(self.window)
        for i, price in enumerate([100.0, 105.0, 95.0, 102.0]):
            self.recorder.observe_contract(
                spec,
                instant_at(self.window.start_utc + timedelta(seconds=30 * i)),
                spot=price,
            )
        key = spec.key
        row = self.recorder.get_contract(key)
        self.assertEqual(row["scan_count"], 4)
        self.assertEqual(row["open_spot"], 100.0)
        self.assertEqual(row["last_spot"], 102.0)
        self.assertEqual(row["high_spot"], 105.0)
        self.assertEqual(row["low_spot"], 95.0)
        self.assertEqual(self.recorder.summary()["contracts_total"], 1)

    def test_no_trade_predictions_are_recorded(self):
        """Audit C-2: V3 never recorded abstentions, so abstention rate and
        the section 26O coverage curves were uncomputable."""
        spec = make_spec(self.window)
        key = self.recorder.observe_contract(spec, instant_at(self.window.start_utc), spot=100.0)

        for i in range(5):
            self.recorder.record_prediction(
                PredictionRecord(
                    key=key,
                    contract_id=self.window.contract_id,
                    asset="BTC-USD",
                    scan_utc=self.window.start_utc + timedelta(seconds=10 * i),
                    spot=100.0 + i,
                    decision="NO_TRADE",
                    decision_reason="PHASE2_NO_MODEL",
                    data_quality_passed=True,
                )
            )

        rows = self.recorder.predictions_for(key)
        self.assertEqual(len(rows), 5)
        self.assertTrue(all(r["decision"] == "NO_TRADE" for r in rows))
        self.assertEqual(self.recorder.summary()["prediction_rows"], 5)

    def test_csv_mirrors_are_written(self):
        spec = make_spec(self.window)
        key = self.recorder.observe_contract(spec, instant_at(self.window.start_utc), spot=100.0)
        self.recorder.record_prediction(
            PredictionRecord(
                key=key, contract_id=self.window.contract_id, asset="BTC-USD",
                scan_utc=self.window.start_utc, spot=100.0,
            )
        )
        self.assertTrue(self.recorder.predictions_csv.exists())
        text = self.recorder.predictions_csv.read_text(encoding="utf-8")
        self.assertIn("decision", text)
        self.assertIn("BTC-USD", text)

    def test_entry_is_recorded_with_timestamp_and_side(self):
        spec = make_spec(self.window)
        key = self.recorder.observe_contract(spec, instant_at(self.window.start_utc), spot=100.0)
        entry_time = self.window.start_utc + timedelta(minutes=3)
        self.recorder.record_entry(key, instant_at(entry_time), "YES", 101.5, "test entry")

        row = self.recorder.get_contract(key)
        self.assertEqual(row["entered"], 1)
        self.assertEqual(row["entry_side"], "YES")
        self.assertEqual(row["entry_spot"], 101.5)
        self.assertIn("2026-08-13", row["entry_time_utc"])


class TestResolution(RecorderTestCase):
    def _engine(self, bars: dict) -> ResolutionEngine:
        return ResolutionEngine(
            self.recorder,
            lambda asset: bars.get(asset),
            grace_seconds=0.0,
            max_attempts=3,
        )

    def test_contract_resolves_yes_when_terminal_above_reference(self):
        spec = make_spec(self.window, reference=100.0)
        key = self.recorder.observe_contract(spec, instant_at(self.window.start_utc), spot=100.0)

        # Rising market: bar at the resolution instant opens well above 100.
        bars = {"BTC-USD": make_bars("BTC-USD", self.window.start_utc, 20, 100.0, step=1.0)}
        engine = self._engine(bars)

        after = instant_at(self.window.end_utc + timedelta(minutes=1))
        resolved = engine.resolve_due(after)

        self.assertEqual(len(resolved), 1)
        row = self.recorder.get_contract(key)
        self.assertEqual(row["status"], ContractStatus.RESOLVED.value)
        self.assertEqual(row["outcome_label"], "YES")
        self.assertEqual(row["outcome_yes"], 1)
        self.assertIsNotNone(row["terminal_price"])
        self.assertIsNotNone(row["resolved_at_utc"])

    def test_contract_resolves_no_when_terminal_below_reference(self):
        spec = make_spec(self.window, reference=200.0)
        key = self.recorder.observe_contract(spec, instant_at(self.window.start_utc), spot=100.0)
        bars = {"BTC-USD": make_bars("BTC-USD", self.window.start_utc, 20, 100.0, step=1.0)}
        engine = self._engine(bars)
        engine.resolve_due(instant_at(self.window.end_utc + timedelta(minutes=1)))

        row = self.recorder.get_contract(key)
        self.assertEqual(row["outcome_label"], "NO")
        self.assertEqual(row["outcome_yes"], 0)

    def test_prediction_correctness_only_scored_when_a_side_was_taken(self):
        """A NO TRADE is neither correct nor incorrect. Scoring it as a loss
        would corrupt every downstream accuracy statistic."""
        spec = make_spec(self.window, reference=100.0)
        key = self.recorder.observe_contract(spec, instant_at(self.window.start_utc), spot=100.0)
        bars = {"BTC-USD": make_bars("BTC-USD", self.window.start_utc, 20, 100.0, step=1.0)}
        self._engine(bars).resolve_due(instant_at(self.window.end_utc + timedelta(minutes=1)))

        row = self.recorder.get_contract(key)
        self.assertEqual(row["outcome_label"], "YES")
        self.assertIsNone(row["prediction_correct"])

    def test_correct_entry_scores_one(self):
        spec = make_spec(self.window, reference=100.0)
        key = self.recorder.observe_contract(spec, instant_at(self.window.start_utc), spot=100.0)
        self.recorder.record_entry(
            key, instant_at(self.window.start_utc + timedelta(minutes=3)), "YES", 103.0
        )
        bars = {"BTC-USD": make_bars("BTC-USD", self.window.start_utc, 20, 100.0, step=1.0)}
        self._engine(bars).resolve_due(instant_at(self.window.end_utc + timedelta(minutes=1)))

        row = self.recorder.get_contract(key)
        self.assertEqual(row["outcome_label"], "YES")
        self.assertEqual(row["prediction_correct"], 1)

    def test_wrong_entry_scores_zero(self):
        spec = make_spec(self.window, reference=100.0)
        key = self.recorder.observe_contract(spec, instant_at(self.window.start_utc), spot=100.0)
        self.recorder.record_entry(
            key, instant_at(self.window.start_utc + timedelta(minutes=3)), "NO", 103.0
        )
        bars = {"BTC-USD": make_bars("BTC-USD", self.window.start_utc, 20, 100.0, step=1.0)}
        self._engine(bars).resolve_due(instant_at(self.window.end_utc + timedelta(minutes=1)))

        row = self.recorder.get_contract(key)
        self.assertEqual(row["prediction_correct"], 0)

    def test_open_contract_is_not_resolved_early(self):
        spec = make_spec(self.window)
        key = self.recorder.observe_contract(spec, instant_at(self.window.start_utc), spot=100.0)
        bars = {"BTC-USD": make_bars("BTC-USD", self.window.start_utc, 20, 100.0)}
        engine = self._engine(bars)

        mid = instant_at(self.window.start_utc + timedelta(minutes=7))
        self.assertEqual(engine.resolve_due(mid), [])
        self.assertEqual(self.recorder.get_contract(key)["status"], ContractStatus.OPEN.value)

    def test_missing_bars_eventually_marks_unresolvable_not_silently_dropped(self):
        """The contract must reach a TERMINAL state. It may never sit in OPEN
        forever, and it may never disappear."""
        spec = make_spec(self.window)
        key = self.recorder.observe_contract(spec, instant_at(self.window.start_utc), spot=100.0)
        engine = self._engine({})   # no bars for any asset

        after = instant_at(self.window.end_utc + timedelta(minutes=1))
        for _ in range(5):
            engine.resolve_due(after)

        row = self.recorder.get_contract(key)
        self.assertEqual(row["status"], ContractStatus.UNRESOLVED_NO_DATA.value)
        self.assertIn("no bars available", row["unresolved_reason"])
        self.assertIsNotNone(row["resolved_at_utc"])
        # Still present -- never deleted (master prompt section 20).
        self.assertEqual(self.recorder.summary()["contracts_total"], 1)

    def test_unknown_settlement_rule_is_unresolvable_not_guessed(self):
        spec = make_spec(self.window, rule=SettlementRule.UNKNOWN)
        key = self.recorder.observe_contract(spec, instant_at(self.window.start_utc), spot=100.0)
        bars = {"BTC-USD": make_bars("BTC-USD", self.window.start_utc, 20, 100.0)}
        self._engine(bars).resolve_due(instant_at(self.window.end_utc + timedelta(minutes=1)))

        row = self.recorder.get_contract(key)
        self.assertEqual(row["status"], ContractStatus.UNRESOLVED_NO_DATA.value)
        self.assertIn("settlement rule unknown", row["unresolved_reason"])
        self.assertIsNone(row["outcome_label"])

    def test_missing_reference_is_unresolvable(self):
        spec = ContractSpec(
            window=self.window, underlying="BTC-USD",
            reference_price=None, reference_source=ReferenceSource.UNAVAILABLE,
            settlement_rule=SettlementRule.PROXY_TERMINAL_ABOVE_REFERENCE,
        )
        key = self.recorder.observe_contract(spec, instant_at(self.window.start_utc), spot=100.0)
        bars = {"BTC-USD": make_bars("BTC-USD", self.window.start_utc, 20, 100.0)}
        self._engine(bars).resolve_due(instant_at(self.window.end_utc + timedelta(minutes=1)))

        row = self.recorder.get_contract(key)
        self.assertEqual(row["status"], ContractStatus.UNRESOLVED_NO_DATA.value)
        self.assertIn("no settlement reference", row["unresolved_reason"])

    def test_many_contracts_all_reach_terminal_status(self):
        """The core guarantee, at scale: nothing is left OPEN once matured."""
        bars_map = {}
        keys = []
        for i in range(8):
            window = ContractWindow.for_instant(
                datetime(2026, 8, 13, 12, 0, tzinfo=UTC) + timedelta(minutes=15 * i), ET, 15
            )
            for asset in ("BTC-USD", "ETH-USD"):
                spec = make_spec(window, asset=asset, reference=100.0)
                keys.append(
                    self.recorder.observe_contract(
                        spec, instant_at(window.start_utc), spot=100.0
                    )
                )
        for asset in ("BTC-USD", "ETH-USD"):
            bars_map[asset] = make_bars(
                asset, datetime(2026, 8, 13, 12, 0, tzinfo=UTC), 140, 100.0, step=0.5
            )

        engine = self._engine(bars_map)
        engine.resolve_due(instant_at(datetime(2026, 8, 13, 16, 0, tzinfo=UTC)))

        self.assertEqual(len(keys), 16)
        for key in keys:
            row = self.recorder.get_contract(key)
            self.assertNotEqual(
                row["status"], ContractStatus.OPEN.value,
                f"{key} was left OPEN after its window matured",
            )
        summary = self.recorder.summary()
        self.assertEqual(summary["contracts_open"], 0)
        self.assertEqual(summary["contracts_resolved"], 16)


class TestRestartRecovery(RecorderTestCase):
    """Audit C-4: V3 held all state in module globals and lost a live position
    entirely on restart, with no record that it had ever existed."""

    def test_contract_open_at_crash_is_resolved_on_next_startup(self):
        spec = make_spec(self.window, reference=100.0)
        key = self.recorder.observe_contract(spec, instant_at(self.window.start_utc), spot=100.0)
        self.recorder.record_entry(
            key, instant_at(self.window.start_utc + timedelta(minutes=2)), "YES", 102.0
        )
        db_path = self.recorder.db_path
        contracts_csv = self.recorder.contracts_csv
        predictions_csv = self.recorder.predictions_csv

        # Simulate a crash: close without resolving.
        self.recorder.close()

        # New process, new recorder, same database.
        self.recorder = ContractRecorder(
            db_path, contracts_csv=contracts_csv, predictions_csv=predictions_csv
        )
        self.assertEqual(len(self.recorder.open_contracts()), 1)

        bars = {"BTC-USD": make_bars("BTC-USD", self.window.start_utc, 20, 100.0, step=1.0)}
        engine = ResolutionEngine(
            self.recorder, lambda asset: bars.get(asset), grace_seconds=0.0
        )
        report = engine.recover_on_startup(
            instant_at(self.window.end_utc + timedelta(hours=1))
        )

        self.assertEqual(report["open_at_startup"], 1)
        self.assertEqual(report["expired_at_startup"], 1)
        self.assertEqual(report["resolved_now"], 1)
        self.assertEqual(report["still_open"], 0)

        row = self.recorder.get_contract(key)
        self.assertEqual(row["status"], ContractStatus.RESOLVED.value)
        self.assertEqual(row["outcome_label"], "YES")
        self.assertEqual(row["prediction_correct"], 1)

    def test_startup_recovery_leaves_current_window_open(self):
        spec = make_spec(self.window)
        self.recorder.observe_contract(spec, instant_at(self.window.start_utc), spot=100.0)
        bars = {"BTC-USD": make_bars("BTC-USD", self.window.start_utc, 20, 100.0)}
        engine = ResolutionEngine(
            self.recorder, lambda asset: bars.get(asset), grace_seconds=0.0
        )
        # Startup happens mid-window: the contract is not yet due.
        report = engine.recover_on_startup(
            instant_at(self.window.start_utc + timedelta(minutes=5))
        )
        self.assertEqual(report["still_open"], 1)
        self.assertEqual(report["resolved_now"], 0)


class TestTerminalPrice(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 8, 13, 15, 0, tzinfo=UTC)
        self.resolution = self.start + timedelta(minutes=15)

    def test_prefers_open_at_or_after_resolution(self):
        bars = make_bars("BTC-USD", self.start, 20, 100.0, step=1.0)
        found = find_terminal_price(bars.frame, self.resolution)
        self.assertIsNotNone(found)
        self.assertEqual(found.source, "OPEN_AT_OR_AFTER")
        # Bar 15 (0-indexed) opens at 100 + 15 = 115.
        self.assertAlmostEqual(found.price, 115.0)
        self.assertAlmostEqual(found.lag_seconds, 0.0)

    def test_falls_back_to_close_before_resolution(self):
        # Only 15 bars: the last one starts at 15:14, none at or after 15:15.
        bars = make_bars("BTC-USD", self.start, 15, 100.0, step=1.0)
        found = find_terminal_price(bars.frame, self.resolution)
        self.assertIsNotNone(found)
        self.assertEqual(found.source, "CLOSE_AT_OR_BEFORE")
        self.assertLess(found.lag_seconds, 0.0)

    def test_refuses_when_no_bar_is_close_enough(self):
        # Bars stop 30 minutes before resolution; tolerance is 180s.
        bars = make_bars("BTC-USD", self.start - timedelta(hours=2), 10, 100.0)
        self.assertIsNone(find_terminal_price(bars.frame, self.resolution))

    def test_empty_frame_returns_none(self):
        self.assertIsNone(find_terminal_price(None, self.resolution))
        self.assertIsNone(find_terminal_price(pd.DataFrame(), self.resolution))


if __name__ == "__main__":
    unittest.main()
