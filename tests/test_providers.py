"""
Provider tests: fallback order, graceful degradation, no fabrication.

Master prompt section 26A requires the chain to prefer official data, fall back
through local/manual feeds, and end at an underlying-only proxy -- while
remaining fully operational when the preferred providers are unavailable.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from mantis_v4.clock import UTC, Instant
from mantis_v4.config import MantisConfig, WebullCredentials
from mantis_v4.contracts import ContractWindow, ReferenceSource, SettlementRule
from mantis_v4.providers.base import (
    BarSet,
    HealthState,
    ProviderError,
    ProviderHealth,
    ProviderUnavailable,
    retry_with_backoff,
)
from mantis_v4.providers.chain import ContractProviderChain
from mantis_v4.providers.local_file import LocalFileContractProvider
from mantis_v4.providers.market_data import YFinanceMarketDataProvider
from mantis_v4.providers.proxy import UnderlyingProxyContractProvider
from mantis_v4.providers.webull import OfficialWebullOpenAPIProvider

ET = ZoneInfo("America/New_York")


def make_frame(start: datetime, minutes: int, base: float = 100.0) -> pd.DataFrame:
    index = pd.date_range(start.astimezone(UTC), periods=minutes, freq="1min", tz="UTC")
    closes = [base + i * 0.5 for i in range(minutes)]
    return pd.DataFrame(
        {
            "Open": closes,
            "High": [c + 1 for c in closes],
            "Low": [c - 1 for c in closes],
            "Close": [c + 0.2 for c in closes],
            "Volume": [5.0] * minutes,
        },
        index=index,
    )


class TestWebullProviderDegradation(unittest.TestCase):
    """The provider must never block MANTIS and must never invent data."""

    def setUp(self):
        self.window = ContractWindow.for_instant(
            datetime(2026, 8, 13, 15, 0, tzinfo=UTC), ET, 15
        )
        self.now = Instant(utc=datetime(2026, 8, 13, 15, 5, tzinfo=UTC), monotonic=0.0)

    def test_no_credentials_reports_auth_not_configured(self):
        provider = OfficialWebullOpenAPIProvider(WebullCredentials())
        self.assertIs(provider.health.state, HealthState.AUTH_NOT_CONFIGURED)
        self.assertFalse(provider.is_operational)
        self.assertIsNone(provider.get_contract_spec("BTC-USD", self.window, self.now))

    def test_status_lines_match_required_wording(self):
        provider = OfficialWebullOpenAPIProvider(WebullCredentials())
        lines = provider.status_lines()
        self.assertIn("WEBULL STATUS:        AUTH NOT CONFIGURED", lines)
        self.assertIn("LIVE CONTRACT QUOTES: UNAVAILABLE", lines)
        self.assertIn("EV ENGINE:            DISABLED", lines)

    def test_credentials_without_sdk_reports_sdk_not_installed(self):
        creds = WebullCredentials(app_key="k", app_secret="s", market_data_enabled=True)
        provider = OfficialWebullOpenAPIProvider(creds, sdk_module=None)
        self.assertIs(provider.health.state, HealthState.SDK_NOT_INSTALLED)
        self.assertIsNone(provider.get_contract_spec("BTC-USD", self.window, self.now))

    def test_credentials_without_market_data_flag_is_unavailable(self):
        creds = WebullCredentials(app_key="k", app_secret="s", market_data_enabled=False)
        provider = OfficialWebullOpenAPIProvider(creds, sdk_module="webull")
        self.assertIs(provider.health.state, HealthState.UNAVAILABLE)

    def test_fully_configured_reports_schema_unverified_and_returns_none(self):
        """We have credentials and an SDK, but the response schema is not
        verified (audit UNKNOWN-4). The honest result is None, not a guess."""
        creds = WebullCredentials(app_key="k", app_secret="s", market_data_enabled=True)
        provider = OfficialWebullOpenAPIProvider(creds, sdk_module="webull")
        self.assertIs(provider.health.state, HealthState.SCHEMA_UNVERIFIED)
        self.assertIsNone(provider.get_contract_spec("BTC-USD", self.window, self.now))

    def test_seam_raises_provider_unavailable(self):
        creds = WebullCredentials(app_key="k", app_secret="s", market_data_enabled=True)
        provider = OfficialWebullOpenAPIProvider(creds, sdk_module="webull")
        with self.assertRaises(ProviderUnavailable):
            provider._fetch_contract_payload("BTC-USD", self.window, self.now)

    def test_credentials_are_redacted_in_repr(self):
        creds = WebullCredentials(app_key="SECRET_KEY_123", app_secret="SECRET_VALUE_456")
        text = repr(creds)
        self.assertNotIn("SECRET_KEY_123", text)
        self.assertNotIn("SECRET_VALUE_456", text)
        self.assertIn("<SET>", text)
        self.assertNotIn("SECRET_KEY_123", repr(MantisConfig(credentials=creds)))


class TestLocalFileProvider(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self._tmp.name)
        self.window = ContractWindow.for_instant(
            datetime(2026, 8, 13, 15, 0, tzinfo=UTC), ET, 15
        )
        self.now = Instant(utc=datetime(2026, 8, 13, 15, 5, tzinfo=UTC), monotonic=0.0)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, records):
        (self.directory / "contracts.json").write_text(
            json.dumps({"contracts": records}), encoding="utf-8"
        )

    def test_reads_a_valid_contract(self):
        self.write([{
            "asset": "BTC-USD",
            "window_start_utc": "2026-08-13T15:00:00Z",
            "reference_price": 63400.0,
            "settlement_rule": "TERMINAL_ABOVE_REFERENCE",
            "yes_bid": 0.54, "yes_ask": 0.55,
            "no_bid": 0.44, "no_ask": 0.46,
            "quote_timestamp": "2026-08-13T15:04:58Z",
        }])
        provider = LocalFileContractProvider(self.directory)
        spec = provider.get_contract_spec("BTC-USD", self.window, self.now)

        self.assertIsNotNone(spec)
        self.assertEqual(spec.reference_price, 63400.0)
        self.assertIs(spec.reference_source, ReferenceSource.MANUAL_LOCAL)
        self.assertIs(spec.settlement_rule, SettlementRule.TERMINAL_ABOVE_REFERENCE)
        self.assertAlmostEqual(spec.quote.yes_ask, 0.55)

    def test_manual_reference_does_not_unlock_economics(self):
        """Typing a strike is not the same as verifying one."""
        self.write([{
            "asset": "BTC-USD",
            "window_start_utc": "2026-08-13T15:00:00Z",
            "reference_price": 63400.0,
            "settlement_rule": "TERMINAL_ABOVE_REFERENCE",
            "yes_bid": 0.54, "yes_ask": 0.55, "no_bid": 0.44, "no_ask": 0.46,
            "quote_timestamp": "2026-08-13T15:04:58Z",
        }])
        provider = LocalFileContractProvider(self.directory)
        spec = provider.get_contract_spec("BTC-USD", self.window, self.now)
        self.assertFalse(spec.reference_is_verified)
        self.assertFalse(spec.economics_available(self.now, 15.0))

    def test_naive_timestamp_is_rejected(self):
        self.write([{
            "asset": "BTC-USD",
            "window_start_utc": "2026-08-13T15:00:00",   # no timezone
            "reference_price": 63400.0,
        }])
        provider = LocalFileContractProvider(self.directory)
        self.assertIsNone(provider.get_contract_spec("BTC-USD", self.window, self.now))

    def test_unknown_asset_returns_none(self):
        self.write([{
            "asset": "BTC-USD",
            "window_start_utc": "2026-08-13T15:00:00Z",
            "reference_price": 63400.0,
        }])
        provider = LocalFileContractProvider(self.directory)
        self.assertIsNone(provider.get_contract_spec("ETH-USD", self.window, self.now))

    def test_wrong_window_returns_none(self):
        self.write([{
            "asset": "BTC-USD",
            "window_start_utc": "2026-08-13T15:00:00Z",
            "reference_price": 63400.0,
        }])
        provider = LocalFileContractProvider(self.directory)
        other = ContractWindow.for_instant(
            datetime(2026, 8, 13, 18, 0, tzinfo=UTC), ET, 15
        )
        self.assertIsNone(provider.get_contract_spec("BTC-USD", other, self.now))

    def test_malformed_json_does_not_raise(self):
        (self.directory / "broken.json").write_text("{not json", encoding="utf-8")
        provider = LocalFileContractProvider(self.directory)
        self.assertIsNone(provider.get_contract_spec("BTC-USD", self.window, self.now))

    def test_missing_directory_does_not_raise(self):
        provider = LocalFileContractProvider(self.directory / "does-not-exist")
        self.assertIsNone(provider.get_contract_spec("BTC-USD", self.window, self.now))
        self.assertIs(provider.health.state, HealthState.UNAVAILABLE)

    def test_updates_are_picked_up(self):
        self.write([{
            "asset": "BTC-USD", "window_start_utc": "2026-08-13T15:00:00Z",
            "reference_price": 100.0,
        }])
        provider = LocalFileContractProvider(self.directory)
        first = provider.get_contract_spec("BTC-USD", self.window, self.now)
        self.assertEqual(first.reference_price, 100.0)

        self.write([{
            "asset": "BTC-USD", "window_start_utc": "2026-08-13T15:00:00Z",
            "reference_price": 200.0,
        }])
        provider._signature = None   # force the mtime check to re-evaluate
        second = provider.get_contract_spec("BTC-USD", self.window, self.now)
        self.assertEqual(second.reference_price, 200.0)


class TestProxyProvider(unittest.TestCase):
    def setUp(self):
        self.window = ContractWindow.for_instant(
            datetime(2026, 8, 13, 15, 0, tzinfo=UTC), ET, 15
        )
        self.now = Instant(utc=datetime(2026, 8, 13, 15, 5, tzinfo=UTC), monotonic=0.0)

    def barset(self, frame) -> BarSet:
        return BarSet(
            asset="BTC-USD", frame=frame, source="test",
            fetched_at_utc=self.now.utc, last_bar_is_partial=True,
        )

    def test_reference_is_window_open_and_flagged_proxy(self):
        frame = make_frame(datetime(2026, 8, 13, 14, 0, tzinfo=UTC), 90, base=100.0)
        provider = UnderlyingProxyContractProvider(lambda a: self.barset(frame))
        spec = provider.get_contract_spec("BTC-USD", self.window, self.now)

        self.assertIsNotNone(spec)
        self.assertIs(spec.reference_source, ReferenceSource.PROXY_WINDOW_OPEN)
        self.assertIs(spec.settlement_rule, SettlementRule.PROXY_TERMINAL_ABOVE_REFERENCE)
        self.assertFalse(spec.reference_is_verified)
        self.assertFalse(spec.economics_available(self.now, 15.0))
        # Bar at 15:00 is index 60 of a series starting 14:00 at base 100.0.
        self.assertAlmostEqual(spec.reference_price, 100.0 + 60 * 0.5)

    def test_no_bars_returns_none(self):
        provider = UnderlyingProxyContractProvider(lambda a: None)
        self.assertIsNone(provider.get_contract_spec("BTC-USD", self.window, self.now))

    def test_missing_opening_bar_returns_none_not_a_substitute(self):
        """V3 substituted the current price when the opening bar was missing,
        silently producing a zero buffer. Returning None is the honest answer."""
        frame = make_frame(datetime(2026, 8, 13, 13, 0, tzinfo=UTC), 30)   # ends 13:29
        provider = UnderlyingProxyContractProvider(lambda a: self.barset(frame))
        self.assertIsNone(provider.get_contract_spec("BTC-USD", self.window, self.now))

    def test_bar_after_window_end_is_refused(self):
        frame = make_frame(datetime(2026, 8, 13, 16, 0, tzinfo=UTC), 30)
        provider = UnderlyingProxyContractProvider(lambda a: self.barset(frame))
        self.assertIsNone(provider.get_contract_spec("BTC-USD", self.window, self.now))

    def test_lookup_exception_does_not_propagate(self):
        def boom(asset):
            raise RuntimeError("upstream exploded")

        provider = UnderlyingProxyContractProvider(boom)
        self.assertIsNone(provider.get_contract_spec("BTC-USD", self.window, self.now))
        self.assertIs(provider.health.state, HealthState.ERROR)


class TestProviderChain(unittest.TestCase):
    def setUp(self):
        self.window = ContractWindow.for_instant(
            datetime(2026, 8, 13, 15, 0, tzinfo=UTC), ET, 15
        )
        self.now = Instant(utc=datetime(2026, 8, 13, 15, 5, tzinfo=UTC), monotonic=0.0)
        self.frame = make_frame(datetime(2026, 8, 13, 14, 0, tzinfo=UTC), 90)

    def build_chain(self, local_dir: Path) -> ContractProviderChain:
        webull = OfficialWebullOpenAPIProvider(WebullCredentials())
        local = LocalFileContractProvider(local_dir)
        proxy = UnderlyingProxyContractProvider(
            lambda a: BarSet(
                asset=a, frame=self.frame, source="test",
                fetched_at_utc=self.now.utc, last_bar_is_partial=True,
            )
        )
        return ContractProviderChain([proxy, webull, local])   # order deliberately shuffled

    def test_priority_order_is_enforced_regardless_of_construction_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            chain = self.build_chain(Path(tmp))
            names = [p.name for p in chain.providers]
            self.assertEqual(names, ["webull-official", "local-file", "underlying-proxy"])

    def test_falls_through_to_proxy_when_nothing_else_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            chain = self.build_chain(Path(tmp))
            spec = chain.get_contract_spec("BTC-USD", self.window, self.now)
            self.assertIsNotNone(spec)
            self.assertEqual(spec.provider_name, "underlying-proxy")
            self.assertEqual(chain.last_served_by["BTC-USD"], "underlying-proxy")

    def test_local_file_wins_over_proxy(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "c.json").write_text(
                json.dumps({"contracts": [{
                    "asset": "BTC-USD",
                    "window_start_utc": "2026-08-13T15:00:00Z",
                    "reference_price": 55555.0,
                    "settlement_rule": "TERMINAL_ABOVE_REFERENCE",
                }]}),
                encoding="utf-8",
            )
            chain = self.build_chain(directory)
            spec = chain.get_contract_spec("BTC-USD", self.window, self.now)
            self.assertEqual(spec.provider_name, "local-file")
            self.assertEqual(spec.reference_price, 55555.0)

    def test_proxy_alone_does_not_count_as_economics_capable(self):
        with tempfile.TemporaryDirectory() as tmp:
            chain = self.build_chain(Path(tmp))
            chain.get_contract_spec("BTC-USD", self.window, self.now)
            self.assertFalse(chain.economics_provider_available())

    def test_exploding_provider_does_not_break_the_chain(self):
        class Exploding(UnderlyingProxyContractProvider):
            name = "exploding"
            priority = 1

            def get_contract_spec(self, asset, window, instant):
                raise RuntimeError("boom")

        proxy = UnderlyingProxyContractProvider(
            lambda a: BarSet(
                asset=a, frame=self.frame, source="test",
                fetched_at_utc=self.now.utc, last_bar_is_partial=True,
            )
        )
        chain = ContractProviderChain([Exploding(lambda a: None), proxy])
        spec = chain.get_contract_spec("BTC-USD", self.window, self.now)
        self.assertIsNotNone(spec)
        self.assertEqual(spec.provider_name, "underlying-proxy")


class TestRetryAndBackoff(unittest.TestCase):
    def test_succeeds_after_transient_failures(self):
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise ConnectionError("transient")
            return "ok"

        result = retry_with_backoff(
            flaky, attempts=5, backoff_seconds=0.0, sleep=lambda s: None
        )
        self.assertEqual(result, "ok")
        self.assertEqual(calls["n"], 3)

    def test_raises_after_exhausting_attempts(self):
        def always_fails():
            raise ConnectionError("down")

        with self.assertRaises(ProviderError):
            retry_with_backoff(
                always_fails, attempts=3, backoff_seconds=0.0, sleep=lambda s: None
            )

    def test_provider_unavailable_is_not_retried(self):
        calls = {"n": 0}

        def structural():
            calls["n"] += 1
            raise ProviderUnavailable("no credentials")

        with self.assertRaises(ProviderUnavailable):
            retry_with_backoff(
                structural, attempts=5, backoff_seconds=0.0, sleep=lambda s: None
            )
        self.assertEqual(calls["n"], 1, "structural failures must not be retried")

    def test_health_tracks_failures(self):
        health = ProviderHealth(name="test")
        health.record_failure("boom")
        health.record_failure("boom again")
        self.assertEqual(health.consecutive_failures, 2)
        health.record_success(datetime(2026, 8, 13, tzinfo=UTC))
        self.assertEqual(health.consecutive_failures, 0)
        self.assertIs(health.state, HealthState.LIVE)


class TestMarketDataProvider(unittest.TestCase):
    """Injected downloader: no network access in tests."""

    def setUp(self):
        self.now = Instant(utc=datetime(2026, 8, 13, 15, 5, tzinfo=UTC), monotonic=0.0)
        self.frame = make_frame(datetime(2026, 8, 13, 13, 30, tzinfo=UTC), 95)

    def test_returns_bars(self):
        provider = YFinanceMarketDataProvider(
            downloader=lambda asset, period: self.frame, max_retries=1
        )
        bars = provider.get_bars("BTC-USD", self.now)
        self.assertIsNotNone(bars)
        self.assertEqual(len(bars), 95)
        self.assertIs(provider.health.state, HealthState.LIVE)

    def test_network_failure_degrades_to_none_without_raising(self):
        def failing(asset, period):
            raise ConnectionError("network down")

        provider = YFinanceMarketDataProvider(
            downloader=failing, max_retries=2, backoff_seconds=0.0
        )
        self.assertIsNone(provider.get_bars("BTC-USD", self.now))
        self.assertIs(provider.health.state, HealthState.ERROR)

    def test_falls_back_to_cache_when_upstream_fails(self):
        state = {"fail": False}

        def sometimes(asset, period):
            if state["fail"]:
                raise ConnectionError("down")
            return self.frame

        provider = YFinanceMarketDataProvider(
            downloader=sometimes, max_retries=1, backoff_seconds=0.0
        )
        self.assertIsNotNone(provider.get_bars("BTC-USD", self.now))

        state["fail"] = True
        cached = provider.get_bars("BTC-USD", self.now)
        self.assertIsNotNone(cached)
        self.assertTrue(cached.from_cache)
        self.assertIs(provider.health.state, HealthState.DEGRADED)

    def test_partial_bar_detection(self):
        provider = YFinanceMarketDataProvider(
            downloader=lambda asset, period: self.frame, max_retries=1
        )
        # Last bar starts 15:04; scanning at 15:04:30 means it is still forming.
        mid = Instant(utc=datetime(2026, 8, 13, 15, 4, 30, tzinfo=UTC), monotonic=0.0)
        self.assertTrue(provider.get_bars("BTC-USD", mid).last_bar_is_partial)

        provider2 = YFinanceMarketDataProvider(
            downloader=lambda asset, period: self.frame, max_retries=1
        )
        later = Instant(utc=datetime(2026, 8, 13, 15, 9, 0, tzinfo=UTC), monotonic=0.0)
        self.assertFalse(provider2.get_bars("BTC-USD", later).last_bar_is_partial)


if __name__ == "__main__":
    unittest.main()
