import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from mantis_v4.clock import Instant
from mantis_v4.contracts import ContractWindow
from mantis_v4.forward import (ForwardEngine, ForwardStore, LiveAssetState,
                               POLICY_IDENTIFIER, ReferenceStatus, audit_contract,
                               daily_report, forward_report, incorrect_signals, manifest)

UTC = timezone.utc
BASE = datetime(2026, 8, 15, 12, 11, tzinfo=UTC)


def live_state(asset="BTC-USD", now=BASE, p=.96, lcb=.92, price=102.0):
    window = ContractWindow.for_instant(Instant(now, 0), ZoneInfo("America/New_York"), 15)
    return LiveAssetState(asset=asset, contract_id=window.contract_id,
        window_start=window.start_utc, window_end=window.end_utc, scan_timestamp=now,
        reference=100.0, reference_status=ReferenceStatus.PROXY,
        current_price=price, volatility_estimate=.01, p_yes=p,
        conservative_bound=lcb, fragility=40.0, disagreement=.01,
        crossing_probability=.10, reference_crossings=2)


class Phase11ForwardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.store = ForwardStore(Path(self.temporary.name))
        self.engine = ForwardEngine(self.store, run_id="phase11-run",
                                    run_metadata={"asset_universe":["BTC-USD"], "config_profile":"default"})

    def tearDown(self): self.temporary.cleanup()

    def resolve(self, state, terminal=103.0):
        self.engine.process(state)
        return self.engine.resolve(asset=state.asset, contract_id=state.contract_id,
            resolution_timestamp=state.window_end, terminal_value=terminal, reference=100.0)

    def test_policy_is_frozen(self):
        self.assertEqual(POLICY_IDENTIFIER, "H_p0.95_l0.90_f50_d.05_t300")

    def test_run_identity_and_metadata(self):
        row = self.store.read("runs")[0]
        self.assertEqual(row["run_id"], "phase11-run")
        self.assertEqual(row["policy_identifier"], POLICY_IDENTIFIER)
        self.assertTrue(row["observation_only"]); self.assertFalse(row["demo"])
        self.assertNotIn("secret", json.dumps(row).lower())

    def test_contract_identity_is_deterministic_at_midnight(self):
        now = datetime(2026, 8, 16, 0, 0, tzinfo=UTC)
        self.assertEqual(live_state(now=now).contract_id, live_state(now=now).contract_id)

    def test_entry_immutable_and_resolution_separate(self):
        state = live_state(); self.engine.process(state)
        before = self.store.path("entries").read_bytes()
        self.engine.resolve(asset=state.asset, contract_id=state.contract_id,
            resolution_timestamp=state.window_end, terminal_value=99, reference=100)
        self.assertEqual(before, self.store.path("entries").read_bytes())
        self.assertEqual(len(self.store.read("resolutions")), 1)

    def test_resolution_is_idempotent_after_restart(self):
        state = live_state(); self.resolve(state)
        restarted = ForwardEngine(ForwardStore(Path(self.temporary.name)), run_id="restart")
        restarted.resolve(asset=state.asset, contract_id=state.contract_id,
            resolution_timestamp=state.window_end, terminal_value=90, reference=100)
        self.assertEqual(len(self.store.read("resolutions")), 1)

    def test_report_counts_and_scores(self):
        self.resolve(live_state(), 103)
        report = forward_report(self.store, n_boot=10)
        self.assertEqual((report["total_entry_events"], report["correct"], report["yes_entries"]), (1, 1, 1))
        self.assertAlmostEqual(report["entry_time_scores"]["brier_score"], .0016)
        self.assertIsNotNone(report["entry_time_scores"]["log_loss"])
        self.assertEqual(report["forward_status"], "SAMPLE TOO SMALL")

    def test_no_signal_and_provider_failure_coverage(self):
        state = live_state(p=.7, lcb=.6); self.engine.process(state)
        self.store.append("window_events", {"window_event_id":"p", "contract_id":"missing",
            "asset":"ETH-USD", "timestamp_utc":BASE.isoformat(), "status":"PROVIDER FAILURE",
            "reason":"PROVIDER UNAVAILABLE"}, "window_event_id")
        report = forward_report(self.store, n_boot=10)
        self.assertEqual(report["no_trade_count"], 1)
        self.assertEqual(report["provider_failure_count"], 1)

    def test_yes_no_asset_and_timing_breakdowns(self):
        yes = live_state(); no = live_state(asset="ETH-USD", p=.03, lcb=.92, price=98)
        self.resolve(yes, 103); self.resolve(no, 99)
        report = forward_report(self.store, n_boot=10)
        self.assertEqual((report["yes_entries"], report["no_entries"]), (1, 1))
        self.assertEqual(report["per_asset"]["ETH-USD"]["entries"], 1)
        self.assertEqual(sum(x["n"] for x in report["entry_timing"].values()), 2)

    def test_calibration_uses_entry_time_prediction(self):
        self.resolve(live_state(), 103)
        band = forward_report(self.store, n_boot=10)["calibration_entry_time"]["0.95-0.975"]
        self.assertEqual(band["predictions"], 1); self.assertAlmostEqual(band["mean_predicted_probability"], .96)

    def test_manifest_hashes_and_counts(self):
        self.resolve(live_state(), 103); value = manifest(self.store)
        self.assertEqual(value["entry_count"], 1); self.assertEqual(value["resolution_count"], 1)
        self.assertEqual(value["files"]["entries"]["sha256"], hashlib.sha256(self.store.path("entries").read_bytes()).hexdigest())
        self.assertIn(POLICY_IDENTIFIER, value["policy_versions"])

    def test_audit_is_read_only(self):
        state = live_state(); self.resolve(state, 99)
        before = {n:self.store.path(n).read_bytes() if self.store.path(n).exists() else b"" for n in self.store.FILES}
        value = audit_contract(self.store, state.contract_id)
        self.assertTrue(value["read_only"]); self.assertEqual(len(value["observations"]), 1)
        self.assertEqual(before, {n:self.store.path(n).read_bytes() if self.store.path(n).exists() else b"" for n in self.store.FILES})

    def test_incorrect_signal_report(self):
        self.resolve(live_state(), 99)
        bad = incorrect_signals(self.store)
        self.assertEqual(len(bad), 1); self.assertEqual(bad[0]["winning_side"], "NO")

    def test_daily_report_filters_utc_date(self):
        self.resolve(live_state(), 103)
        self.assertEqual(daily_report(self.store, "2026-08-15", n_boot=10)["total_entry_events"], 1)
        self.assertEqual(daily_report(self.store, "2026-08-14", n_boot=10)["total_entry_events"], 0)

    def test_truncated_tail_recovery_with_new_stream(self):
        self.store.path("window_events").write_text('{"window_event_id":"ok"}\n{"bad"', encoding="utf-8")
        self.assertEqual(len(ForwardStore(Path(self.temporary.name)).read("window_events")), 1)

    def test_demo_module_cannot_open_forward_store(self):
        text = (Path(__file__).parents[1]/"mantis_v4/ui/demo.py").read_text(encoding="utf-8")
        self.assertNotIn("from ..forward.store", text); self.assertNotIn("ForwardStore(", text)
        self.assertNotIn("ForwardEngine(", text)

    def test_ui_has_restrained_forward_panel(self):
        text = (Path(__file__).parents[1]/"mantis_v4/ui/web/modules.js").read_text(encoding="utf-8")
        for token in ("FORWARD SAMPLE", "COVERAGE", "DRIFT"):
            self.assertIn(token, text)


if __name__ == "__main__": unittest.main()
