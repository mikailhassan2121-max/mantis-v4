from __future__ import annotations

import json
import tempfile
import unittest
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from mantis_v4.manual_signal.core import (POLICY_VERSION, V2_POLICY, V21_POLICY, V22_POLICY,
    evaluate_candidate)
from mantis_v4.manual_signal.telemetry import (PolicyTelemetryStore,
    policy_observation)
from tests.test_kalshi_manual_signal import ASSETS, NOW, fees, mapping, quote, snapshot


def record(asset, policy, *, selected=False, eligible=True):
    snap=snapshot(asset,p=.97,lcb=.94,seconds=500)
    candidate=evaluate_candidate(snapshot=snap,mapping=mapping(asset),quote=quote(asset),
        quote_status="READY",dev_p95_bps=Decimal("5"),fee_metadata=fees(asset),now=NOW,policy=policy)
    if not eligible:candidate.update(status="CONF FAIL",economically_valid=False)
    row=policy_observation(policy=policy,snapshot=snap,mapping=mapping(asset),quote=quote(asset),
        quote_status="READY",fee_metadata=fees(asset),now=NOW,dev_p95_bps=Decimal("5"),
        final_candidate=candidate)
    row["policy_selected_this_scan"]=selected
    return row


class PolicyTelemetryTests(unittest.TestCase):
    def test_complete_fee_and_reconstructable_economics(self):
        row=record("BTC-USD",V21_POLICY,selected=True)
        for key in ("fee_type","fee_multiplier","fee_schedule_provenance","computed_fee",
                    "total_entry_cost","point_break_even","conservative_break_even",
                    "point_edge","conservative_edge","point_ev","conservative_ev","gates"):
            self.assertIsNotNone(row[key],key)
        self.assertEqual(Decimal(row["side_specific_ask"])+Decimal(row["computed_fee"]),
                         Decimal(row["total_entry_cost"]))
        self.assertTrue(row["final_candidate_qualification"])
        self.assertTrue(all({"gate","passed","value","threshold","operator"}<=set(g) for g in row["gates"]))

    def test_same_synchronized_scan_has_exact_policy_asset_grid(self):
        rows=[]
        for asset in ASSETS:
            rows.extend((record(asset,V2_POLICY),record(asset,V21_POLICY),record(asset,V22_POLICY)))
        self.assertEqual(len({r["scan_id"] for r in rows}),1)
        self.assertEqual({(r["asset"],r["policy_version"]) for r in rows},
                         {(a,p.version) for a in ASSETS for p in (V2_POLICY,V21_POLICY,V22_POLICY)})
        with tempfile.TemporaryDirectory() as tmp:
            store=PolicyTelemetryStore(Path(tmp)); store.append_scan(rows)
            self.assertEqual(len((Path(tmp)/"policy_observations.jsonl").read_text().splitlines()),12)

    def test_explicit_no_signal_completion_and_restart_dedupe(self):
        rows=[record(asset,policy,eligible=False) for asset in ASSETS for policy in (V2_POLICY,V21_POLICY,V22_POLICY)]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); store=PolicyTelemetryStore(root); store.append_scan(rows)
            store.complete_before((NOW+timedelta(minutes=4)).isoformat(),(V2_POLICY,V21_POLICY,V22_POLICY))
            completed=[json.loads(x) for x in (root/"policy_windows.jsonl").read_text().splitlines()]
            self.assertEqual(len(completed),3)
            self.assertTrue(all(r["explicit_no_signal"] and not r["signal_issued"] for r in completed))
            restarted=PolicyTelemetryStore(root)
            restarted.complete_before((NOW+timedelta(minutes=5)).isoformat(),(V2_POLICY,V21_POLICY,V22_POLICY))
            self.assertEqual(len((root/"policy_windows.jsonl").read_text().splitlines()),3)
            summary=restarted.summary(V2_POLICY,V21_POLICY,V22_POLICY)
            self.assertEqual(summary["completed_windows"],1)
            self.assertEqual(summary["v2"]["no_signals"],1)
            self.assertEqual(summary["v21"]["no_signals"],1)
            self.assertEqual(summary["v22"]["no_signals"],1)

    def test_one_completion_per_policy_and_selected_first_timestamp(self):
        rows=[]
        for asset in ASSETS:
            rows.extend((record(asset,V2_POLICY,selected=asset=="ETH-USD"),
                         record(asset,V21_POLICY,selected=asset=="SOL-USD"),
                         record(asset,V22_POLICY,selected=asset=="XRP-USD")))
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); store=PolicyTelemetryStore(root); store.append_scan(rows)
            store.complete_window(rows[0]["window_start_utc"],rows[0]["window_end_utc"],(V2_POLICY,V21_POLICY,V22_POLICY))
            store.complete_window(rows[0]["window_start_utc"],rows[0]["window_end_utc"],(V2_POLICY,V21_POLICY,V22_POLICY))
            completed=[json.loads(x) for x in (root/"policy_windows.jsonl").read_text().splitlines()]
            self.assertEqual(len(completed),3)
            selected={r["policy_version"]:r["selected_asset"] for r in completed}
            self.assertEqual(selected[V2_POLICY.version],"ETH-USD")
            self.assertEqual(selected[V21_POLICY.version],"SOL-USD")
            self.assertEqual(selected[V22_POLICY.version],"XRP-USD")

    def test_frozen_policies_no_forcing_and_bounded_state(self):
        self.assertEqual(POLICY_VERSION,"EXPERIMENTAL_MANUAL_SIGNAL_V2")
        self.assertEqual((V2_POLICY.confidence,V2_POLICY.min_conservative_edge),(.95,Decimal("0.02")))
        self.assertEqual((V21_POLICY.confidence,V21_POLICY.min_conservative_edge),(.92,Decimal("0.015")))
        with tempfile.TemporaryDirectory() as tmp:
            store=PolicyTelemetryStore(Path(tmp))
            self.assertEqual(store._observation_ids.maxlen,4096)
            self.assertEqual(store._completion_ids.maxlen,4096)
        source=Path("mantis_v4/manual_signal/telemetry.py").read_text(encoding="utf-8")
        self.assertIn('"authentication":"NONE"',source)
        self.assertIn('"order_capability":"DISABLED"',source)
        self.assertNotIn("place_order",source); self.assertNotIn("submit_order",source)


if __name__=="__main__":unittest.main()
