"""
Configuration and engine-integration tests.

Includes the anti-regression test for audit finding F-1: V3 shipped 21 tuning
constants that were referenced exactly once -- at their own definition -- and
did nothing. This suite asserts that V4's configuration surface stays wired.
"""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from mantis_v4.clock import UTC, Clock, FrozenClock, Instant, LoopPacer, format_seconds
from mantis_v4.clock import TimezoneUnavailable, load_timezone
from mantis_v4.config import (
    ENV_APP_KEY,
    ENV_APP_SECRET,
    ENV_MARKET_DATA_ENABLED,
    PROJECT_ROOT,
    MantisConfig,
    WebullCredentials,
    credential_status_lines,
)
from mantis_v4.contracts import ContractStatus, ContractWindow
from mantis_v4.engine import PHASE2_DECISION, MantisEngine
from mantis_v4.providers.base import BarSet, MarketDataProvider
from mantis_v4.providers.chain import ContractProviderChain
from mantis_v4.providers.proxy import UnderlyingProxyContractProvider
from mantis_v4.recording import ContractRecorder

ET = ZoneInfo("America/New_York")


class TestConfigLoading(unittest.TestCase):
    def test_defaults_are_valid(self):
        config = MantisConfig.load(local_file=Path("/nonexistent"), environ={})
        config.validate()
        self.assertIn("BTC-USD", config.active_assets)
        self.assertNotIn("ADA-USD", config.active_assets) # historical only; excluded live
        self.assertIn("XRP-USD", config.active_assets)   # preserved, configurable

    def test_enabled_assets_filters_display_set(self):
        config = MantisConfig(enabled_assets=["BTC-USD", "ETH-USD"])
        self.assertEqual(config.active_assets, ["BTC-USD", "ETH-USD"])

    def test_environment_overrides_local_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "local.json"
            path.write_text(
                json.dumps({"credentials": {"app_key": "from-file", "app_secret": "file-secret"}}),
                encoding="utf-8",
            )
            config = MantisConfig.load(
                local_file=path,
                environ={ENV_APP_KEY: "from-env", ENV_MARKET_DATA_ENABLED: "true"},
            )
            self.assertEqual(config.credentials.app_key, "from-env")
            self.assertEqual(config.credentials.app_secret, "file-secret")
            self.assertTrue(config.credentials.market_data_enabled)

    def test_unknown_key_is_rejected_not_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "local.json"
            path.write_text(json.dumps({"totally_made_up_setting": 5}), encoding="utf-8")
            with self.assertRaises(ValueError):
                MantisConfig.load(local_file=path, environ={})

    def test_malformed_local_file_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "local.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(ValueError):
                MantisConfig.load(local_file=path, environ={})

    def test_missing_local_file_is_silent(self):
        config = MantisConfig.load(local_file=Path("/definitely/not/here.json"), environ={})
        self.assertFalse(config.credentials.is_configured)

    def test_market_data_flag_parsing(self):
        for value, expected in [
            ("1", True), ("true", True), ("YES", True), ("on", True),
            ("0", False), ("false", False), ("", False), ("nonsense", False),
        ]:
            config = MantisConfig.load(
                local_file=Path("/nonexistent"),
                environ={ENV_MARKET_DATA_ENABLED: value},
            )
            self.assertEqual(config.credentials.market_data_enabled, expected, value)

    def test_validation_rejects_bad_values(self):
        with self.assertRaises(ValueError):
            MantisConfig(contract_window_minutes=7).validate()   # does not divide an hour
        with self.assertRaises(ValueError):
            MantisConfig(scan_interval_seconds=0).validate()
        with self.assertRaises(ValueError):
            MantisConfig(min_model_confidence=1.5).validate()
        with self.assertRaises(ValueError):
            MantisConfig(enabled_assets=[]).validate()
        with self.assertRaises(ValueError):
            MantisConfig(record_every_n_scans=0).validate()

    def test_credential_status_lines(self):
        unset = credential_status_lines(MantisConfig())
        self.assertIn("WEBULL STATUS:        AUTH NOT CONFIGURED", unset)
        self.assertIn("EV ENGINE:            DISABLED", unset)

        configured = credential_status_lines(
            MantisConfig(credentials=WebullCredentials(
                app_key="k", app_secret="s", market_data_enabled=True
            ))
        )
        self.assertTrue(any("MARKET DATA ENABLED" in line for line in configured))

    def test_secrets_never_appear_in_config_repr(self):
        config = MantisConfig(
            credentials=WebullCredentials(app_key="AKIA_LEAK", app_secret="SHHH_LEAK")
        )
        text = repr(config)
        self.assertNotIn("AKIA_LEAK", text)
        self.assertNotIn("SHHH_LEAK", text)


class TestNoDeadConfig(unittest.TestCase):
    """Audit F-1 anti-regression.

    V3 defined 21 constants that were never referenced anywhere but their own
    definition line. This asserts V4's config fields are actually consumed.
    """

    def test_every_config_field_is_consumed_somewhere(self):
        """Mirrors the exact Phase 1 grep that exposed V3's dead config.

        In V3, 21 constants matched their own name exactly ONCE across the whole
        file -- the definition line. Here we count matches across the entire
        package plus the entry point and require at least two: the definition,
        and at least one real consumer (a property in config.py counts, since
        that property is itself consumed).
        """
        from dataclasses import fields

        sources = [
            path.read_text(encoding="utf-8")
            for path in (PROJECT_ROOT / "mantis_v4").rglob("*.py")
        ]
        entry_point = PROJECT_ROOT / "mantis_15m_resolution_v4.py"
        if entry_point.exists():
            sources.append(entry_point.read_text(encoding="utf-8"))
        combined = "\n".join(sources)

        # Fields whose only consumer arrives in a later phase. Listing them
        # explicitly keeps "not yet used" a deliberate, reviewed statement
        # rather than an accident -- which is precisely how V3 accumulated 21
        # inert knobs.
        phase_deferred = {
            # Phase 7 owns entry/exit thresholds.
            "min_model_confidence", "min_entry_elapsed_seconds",
            "no_new_entry_seconds", "force_exit_seconds",
            "emergency_opposite_confidence",
            # Phase 8 owns EV gating.
            "min_model_edge", "min_expected_value", "max_spread",
            # Phase 9 owns the full UI and the legacy log mirrors.
            "log_csv", "ledger_csv", "model_metrics_json",
            "voice_enabled", "plain_cmd_mode",
        }

        dead = []
        for field in fields(MantisConfig):
            if field.name in phase_deferred:
                continue
            hits = len(re.findall(rf"\b{re.escape(field.name)}\b", combined))
            if hits < 2:
                dead.append((field.name, hits))

        self.assertEqual(
            dead, [],
            f"config fields defined but never consumed (audit F-1 regression): {dead}",
        )

    def test_phase_deferred_fields_are_still_reachable(self):
        """Deferred is not the same as forgotten: they must still be settable."""
        config = MantisConfig(min_model_edge=0.25, max_spread=0.05)
        self.assertAlmostEqual(config.min_model_edge, 0.25)
        self.assertAlmostEqual(config.max_spread, 0.05)

    def test_phase_deferred_thresholds_match_v3_values(self):
        """Phase 2 must not change decision behaviour."""
        config = MantisConfig()
        self.assertAlmostEqual(config.min_model_confidence, 0.80)        # V3 0.80
        self.assertAlmostEqual(config.min_entry_elapsed_seconds, 150.0)  # V3 2.5 min
        self.assertAlmostEqual(config.no_new_entry_seconds, 45.0)        # V3 0.75 min
        self.assertAlmostEqual(config.emergency_opposite_confidence, 0.82)


class TestClock(unittest.TestCase):
    def test_timezone_failure_is_loud(self):
        with self.assertRaises(TimezoneUnavailable):
            load_timezone("Not/AZone")

    def test_instant_is_captured_once(self):
        clock = FrozenClock(datetime(2026, 8, 13, 15, 0, tzinfo=UTC))
        first = clock.capture()
        second = clock.capture()
        self.assertEqual(first.utc, second.utc)   # frozen: no drift between reads

    def test_frozen_clock_advances_explicitly(self):
        clock = FrozenClock(datetime(2026, 8, 13, 15, 0, tzinfo=UTC))
        clock.advance(900)
        self.assertEqual(clock.now_utc(), datetime(2026, 8, 13, 15, 15, tzinfo=UTC))

    def test_frozen_clock_sleep_does_not_block(self):
        clock = FrozenClock(datetime(2026, 8, 13, 15, 0, tzinfo=UTC))
        clock.sleep(3600)
        self.assertEqual(clock.now_utc(), datetime(2026, 8, 13, 16, 0, tzinfo=UTC))

    def test_pacer_sleeps_only_the_remainder(self):
        clock = FrozenClock(datetime(2026, 8, 13, 15, 0, tzinfo=UTC))
        pacer = LoopPacer(clock, interval_seconds=5.0)
        start = clock.monotonic()
        clock.advance(2.0)                      # simulate 2s of work
        work = pacer.pace(start)
        self.assertAlmostEqual(work, 2.0)
        self.assertAlmostEqual(clock.monotonic() - start, 5.0)
        self.assertEqual(pacer.overrun_count, 0)

    def test_pacer_records_overrun(self):
        clock = FrozenClock(datetime(2026, 8, 13, 15, 0, tzinfo=UTC))
        pacer = LoopPacer(clock, interval_seconds=5.0)
        start = clock.monotonic()
        clock.advance(9.0)                      # work took longer than the interval
        pacer.pace(start)
        self.assertEqual(pacer.overrun_count, 1)

    def test_format_seconds(self):
        self.assertEqual(format_seconds(0), "00:00")
        self.assertEqual(format_seconds(65), "01:05")
        self.assertEqual(format_seconds(900), "15:00")
        self.assertEqual(format_seconds(-5), "00:00")


class StubMarketData(MarketDataProvider):
    name = "stub"

    def __init__(self, frame):
        super().__init__()
        self.frame = frame
        self.calls = 0

    def get_bars(self, asset, instant):
        self.calls += 1
        return BarSet(
            asset=asset, frame=self.frame, source=self.name,
            fetched_at_utc=instant.utc, last_bar_is_partial=False,
        )


class TestEngineIntegration(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)

        start = datetime(2026, 8, 13, 13, 30, tzinfo=UTC)
        index = pd.date_range(start, periods=120, freq="1min", tz="UTC")
        closes = [100.0 + (i % 7) * 0.5 + i * 0.02 for i in range(120)]
        frame = pd.DataFrame(
            {
                "Open": closes,
                "High": [c + 0.6 for c in closes],
                "Low": [c - 0.6 for c in closes],
                "Close": closes,
                "Volume": [7.0] * 120,
            },
            index=index,
        )

        self.clock = FrozenClock(datetime(2026, 8, 13, 15, 5, 0, tzinfo=UTC))
        self.market = StubMarketData(frame)
        self.recorder = ContractRecorder(base / "engine.db", model_version="test")

        holder = {}
        proxy = UnderlyingProxyContractProvider(lambda a: holder["engine"]._lookup_bars(a))
        chain = ContractProviderChain([proxy])

        self.config = MantisConfig(enabled_assets=["BTC-USD", "ETH-USD"])
        self.engine = MantisEngine(
            self.config,
            clock=self.clock,
            market_data=self.market,
            contracts=chain,
            recorder=self.recorder,
        )
        holder["engine"] = self.engine

    def tearDown(self):
        self.recorder.close()
        self._tmp.cleanup()

    def test_scan_produces_no_trade_in_phase_2(self):
        result = self.engine.scan()
        self.assertEqual(len(result.assets), 2)
        for scan in result.assets.values():
            self.assertEqual(scan.decision, PHASE2_DECISION)

    def test_scan_records_contracts_and_predictions(self):
        self.engine.scan()
        summary = self.recorder.summary()
        self.assertEqual(summary["contracts_total"], 2)      # two assets, one window
        self.assertEqual(summary["prediction_rows"], 2)
        self.assertEqual(summary["contracts_open"], 2)

    def test_one_fetch_per_asset_per_scan(self):
        """The proxy provider and the quality gate share the scan's bar cache."""
        self.market.calls = 0
        self.engine.scan()
        self.assertEqual(self.market.calls, 2, "expected exactly one fetch per asset")

    def test_all_assets_share_one_window(self):
        result = self.engine.scan()
        ids = {
            scan.spec.contract_id
            for scan in result.assets.values() if scan.spec is not None
        }
        self.assertEqual(len(ids), 1, "every asset in a scan must share one window")

    def test_rollover_is_detected_and_old_contract_resolves(self):
        self.engine.scan()
        open_before = self.recorder.summary()["contracts_open"]
        self.assertEqual(open_before, 2)

        # Advance past the boundary and beyond the resolution grace period.
        self.clock.advance(15 * 60)
        result = self.engine.scan()
        self.assertTrue(result.rolled_over)

        # New window observed; the old one must not still be OPEN forever.
        self.clock.advance(self.config.resolution_grace_seconds + 60)
        self.engine.scan()

        rows = self.recorder.open_contracts()
        stale = [
            r for r in rows
            if r["window_end_utc"] < self.clock.now_utc().isoformat()
        ]
        self.assertEqual(stale, [], "a matured contract was left OPEN")

    def test_scan_survives_a_market_data_failure(self):
        class Broken(MarketDataProvider):
            name = "broken"

            def get_bars(self, asset, instant):
                return None

        self.engine.market_data = Broken()
        self.engine._bars_this_scan = {}
        result = self.engine.scan()
        for scan in result.assets.values():
            self.assertFalse(scan.tradeable)
            self.assertIn("DATA HOLD", scan.decision_reason)


if __name__ == "__main__":
    unittest.main()
