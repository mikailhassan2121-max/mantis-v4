"""Phase 10 production hardening, lifecycle and failure-injection tests."""
from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from mantis_v4 import __version__
from mantis_v4.clock import Instant
from mantis_v4.config import MantisConfig
from mantis_v4.forward import ForwardStore
from mantis_v4.health import exit_code, self_test
from mantis_v4.providers.market_data import YFinanceMarketDataProvider
from mantis_v4.runtime import shutdown_lines, start_worker
from mantis_v4.soak import run_soak
from mantis_v4.ui import CommandCenterState, PresentationConfig
from mantis_v4.ui import errors as ui_errors, theme, webshell
from mantis_v4.ui.state import SystemError
from mantis_v4.ui.webserver import CommandCenterServer, MAX_SSE_CLIENTS

UTC = timezone.utc


class ConfigHardeningTests(unittest.TestCase):
    def test_invalid_assets_timezone_and_runtime_ranges_fail_friendly(self):
        bad = (
            {"assets": ["btc"]}, {"enabled_assets": ["DOGE-USD"]},
            {"contract_timezone": "Mars/Olympus"}, {"max_retries": 0},
            {"network_timeout_seconds": 0}, {"data_dir": "../outside"},
        )
        for values in bad:
            config = MantisConfig()
            for key, value in values.items():
                setattr(config, key, value)
            with self.assertRaises(ValueError, msg=str(values)):
                config.validate()

    def test_unknown_webull_boolean_fails_safe_to_disabled(self):
        config = MantisConfig.load(environ={"WEBULL_MARKET_DATA_ENABLED": "perhaps"})
        self.assertFalse(config.credentials.market_data_enabled)

    def test_small_profiles_only_change_presentation(self):
        quiet = PresentationConfig().apply_profile("quiet")
        self.assertFalse(quiet.audio_enabled); self.assertFalse(quiet.voice_enabled)
        diagnostic = PresentationConfig().apply_profile("diagnostic")
        self.assertTrue(diagnostic.show_advanced_diagnostics)
        with self.assertRaises(ValueError): PresentationConfig().apply_profile("turbo")

    def test_phase10_operator_modes_are_exposed(self):
        from mantis_v4_live import build_parser
        parser = build_parser()
        health = parser.parse_args(["--health-check"])
        self_test_args = parser.parse_args(["--self-test"])
        quiet = parser.parse_args(["--profile", "quiet"])
        self.assertTrue(health.health_check)
        self.assertTrue(self_test_args.self_test)
        self.assertEqual(quiet.profile, "quiet")


class ProviderRecoveryTests(unittest.TestCase):
    def test_exhausted_provider_enters_cooldown_without_hammering_api(self):
        clock = [10.0]
        calls = []

        def unavailable(asset, period):
            calls.append((asset, period))
            raise TimeoutError("simulated timeout")

        provider = YFinanceMarketDataProvider(
            min_candles=2,
            max_retries=1,
            backoff_seconds=0,
            cooldown_seconds=30,
            downloader=unavailable,
            monotonic=lambda: clock[0],
        )
        instant = Instant(datetime.now(UTC), clock[0])
        self.assertIsNone(provider.get_bars("BTC-USD", instant))
        self.assertEqual(len(calls), 1)
        self.assertIsNone(provider.get_bars("BTC-USD", instant))
        self.assertEqual(len(calls), 1)
        clock[0] = 41.0
        self.assertIsNone(provider.get_bars("BTC-USD", Instant(datetime.now(UTC), clock[0])))
        self.assertEqual(len(calls), 2)


class LifecycleTests(unittest.TestCase):
    def test_worker_is_owned_stoppable_and_joined(self):
        def worker(stop):
            while not stop.wait(0.005):
                pass
        handle = start_worker("phase10-test", worker)
        self.assertTrue(handle.thread.daemon)
        self.assertTrue(handle.stop())
        self.assertFalse(handle.thread.is_alive())

    def test_shutdown_report_is_concise_and_complete(self):
        text = "\n".join(shutdown_lines(forward_safe=True, server=True, reporter=True,
                                          audio=True, voice=True, browser=True))
        for phrase in ("MANTIS SHUTDOWN", "FORWARD LOGS", "SERVER", "REPORTER",
                       "AUDIO", "VOICE", "BROWSER"):
            self.assertIn(phrase, text)
        self.assertNotIn("Traceback", text)


class DiskRecoveryTests(unittest.TestCase):
    def test_truncated_final_jsonl_is_ignored_without_rewrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = ForwardStore(Path(temporary))
            row = {"run_id": "one"}
            store.append("runs", row, "run_id")
            with store.path("runs").open("a", encoding="utf-8") as handle:
                handle.write('{"run_id":"partial"')
            before = store.path("runs").read_bytes()
            self.assertEqual(store.read("runs"), [row])
            self.assertEqual(store.path("runs").read_bytes(), before)

    def test_malformed_interior_jsonl_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "runs.jsonl"
            path.write_text('{"run_id":"a"}\nnot-json\n{"run_id":"b"}\n', encoding="utf-8")
            with self.assertRaises(ValueError): ForwardStore(Path(temporary))

    def test_diagnostic_log_rotates_but_forward_files_do_not(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "diagnostic.log"
            path.write_text("x" * 128, encoding="utf-8")
            error = SystemError(datetime.now(UTC), "test", "disk", "boom", "retry", "trace")
            ui_errors.write_diagnostic(error, path, max_bytes=64, backups=2)
            self.assertTrue(path.exists()); self.assertTrue(Path(str(path) + ".1").exists())


class ServerBrowserHardeningTests(unittest.TestCase):
    def test_non_loopback_bind_is_rejected(self):
        state = CommandCenterState(["BTC-USD"], PresentationConfig())
        with self.assertRaises(ValueError):
            CommandCenterServer(state, PresentationConfig(), host="0.0.0.0")

    def test_sse_clients_are_bounded(self):
        state = CommandCenterState(["BTC-USD"], PresentationConfig())
        server = CommandCenterServer(state, PresentationConfig())
        clients = [server.register() for _ in range(MAX_SSE_CLIENTS)]
        try:
            with self.assertRaises(ConnectionError): server.register()
        finally:
            for client in clients: server.unregister(client)

    def test_browser_rejects_nonlocal_url_and_prevents_duplicate_launch(self):
        webshell._ACTIVE_PROCESS = None
        self.assertIsNone(webshell.launch("https://example.com", browser="edge"))
        process = unittest.mock.MagicMock()
        process.poll.return_value = None
        with patch("mantis_v4.ui.webshell.subprocess.Popen", return_value=process) as popen:
            first = webshell.launch("http://127.0.0.1:8123/", browser="edge")
            second = webshell.launch("http://127.0.0.1:8123/", browser="edge")
        self.assertIs(first, second); popen.assert_called_once()
        webshell._ACTIVE_PROCESS = None


class HealthAndSoakTests(unittest.TestCase):
    def test_self_test_isolated_and_passes(self):
        results = self_test(Path(__file__).resolve().parents[1])
        self.assertEqual(exit_code(results), 0, results)
        self.assertIn("real forward directory untouched",
                      next(r.detail for r in results if r.component == "TEMP PERSISTENCE"))

    def test_accelerated_hundreds_of_scans_remain_consistent(self):
        report = run_soak(windows=100, scans_per_window=2)
        self.assertTrue(report.passed, report)
        self.assertEqual(report.scans, 200)
        self.assertLess(report.peak_memory_bytes, 64 * 1024 * 1024)

    def test_version_is_consistent_while_phase8_provenance_stays_frozen(self):
        from mantis_v4.forward.engine import ForwardConfig
        self.assertEqual(__version__, "4.10.0")
        self.assertIn(__version__, theme.SOFTWARE_VERSION)
        self.assertEqual(ForwardConfig().software_version, "MANTIS_V4_PHASE8")


if __name__ == "__main__": unittest.main()
