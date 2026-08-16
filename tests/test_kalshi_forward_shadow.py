from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from mantis_v4.kalshi_forward import (ACTIONABILITY, ASSETS, REFERENCE_POLICY,
    SHADOW_POLICIES, ShadowStore, audit_store, build_observation,
    build_resolution, deterministic_contract_id, shadow_report, simulate_shadow)
from mantis_v4.kalshi_forward.live import load_empirical_bounds

SCRATCH=Path(__file__).parents[1]/"data"/"test-temp"
SCRATCH.mkdir(parents=True,exist_ok=True)

def temporary(): return tempfile.TemporaryDirectory(dir=SCRATCH)


def fixture():
    start=datetime(2026,8,15,12,0,tzinfo=UTC); end=start+timedelta(minutes=15)
    mapping=SimpleNamespace(asset="BTC-USD",series_ticker="KXBTC15M",event_ticker="EV",
        market_ticker="MK",window_start_utc=start,window_end_utc=end,target=Decimal("100.00"),
        settlement_reference_provenance="KALSHI_CRYPTO15M_RULES")
    snap=SimpleNamespace(asset="BTC-USD",timestamp_utc=(end-timedelta(seconds=240)).isoformat(),
        current_price=100.08,p_yes=.97,p_no=.03,predicted_side="YES",conservative_bound=.93,
        fragility=20.,disagreement=.01,crossing_probability=.1,reference_crossings=1,
        seconds_remaining=240.,volatility_estimate=.002)
    quote=SimpleNamespace(quote_verified=True,provider="KALSHI_PUBLIC_REST",authenticated=False,
        yes_bid=Decimal(".70"),yes_ask=Decimal(".72"),yes_ask_size=Decimal("4.5"),
        no_bid=Decimal(".28"),no_ask=Decimal(".30"),no_ask_size=Decimal("3"),
        received_at_utc=snap.timestamp_utc,quote_age_seconds=Decimal("0"),
        quote_timestamp_provenance="LOCAL_RECEIVED_AT_NO_EXCHANGE_TIMESTAMP")
    bounds={a:{"P50":Decimal("2"),"P95":Decimal("7"),"P99":Decimal("12")} for a in ASSETS}
    return mapping,snap,quote,bounds


class KalshiForwardShadowTests(unittest.TestCase):
    def test_universe_exactly_four_no_ada(self):
        self.assertEqual(ASSETS,("BTC-USD","ETH-USD","SOL-USD","XRP-USD")); self.assertNotIn("ADA-USD",ASSETS)

    def test_deterministic_contract_id_and_window_validation(self):
        m,*_=fixture(); a=deterministic_contract_id(m.asset,m.window_start_utc,m.window_end_utc)
        self.assertEqual(a,deterministic_contract_id(m.asset,m.window_start_utc,m.window_end_utc))
        with self.assertRaises(ValueError): deterministic_contract_id(m.asset,m.window_start_utc,m.window_end_utc+timedelta(minutes=1))

    def test_observation_provenance_target_and_policy(self):
        m,s,q,b=fixture(); o,c=build_observation(run_id="r",snapshot=s,mapping=m,quote=q,empirical_bounds=b)
        self.assertEqual(o["kalshi_target"],"100.00"); self.assertEqual(o["current_value_provenance"],"YAHOO_PROXY")
        self.assertEqual(o["event_reference_provenance"],"KALSHI_CRYPTO15M_CF_BENCHMARKS")
        self.assertEqual(o["policy_version"],REFERENCE_POLICY); self.assertEqual(o["actionability"],ACTIONABILITY)
        self.assertFalse(c["production_authorization"]); self.assertEqual(set(c["shadow_policy_results"]),set(SHADOW_POLICIES))

    def test_all_predeclared_sensitivity_bands(self):
        m,s,q,b=fixture(); o,_=build_observation(run_id="r",snapshot=s,mapping=m,quote=q,empirical_bounds=b)
        self.assertEqual(set(o["reference_risk"]),{"fixed_1bp","fixed_2bp","fixed_5bp","fixed_10bp","dev_p50","dev_p95","dev_p99"})
        self.assertEqual(o["reference_risk"]["fixed_5bp"],"REFERENCE_ROBUST")
        self.assertEqual(o["reference_risk"]["fixed_10bp"],"REFERENCE_AMBIGUOUS")

    def test_missing_empirical_bounds_are_unknown_never_robust(self):
        m,s,q,_=fixture(); o,c=build_observation(run_id="r",snapshot=s,mapping=m,quote=q,empirical_bounds={})
        self.assertEqual(o["reference_risk"]["dev_p95"],"REFERENCE_UNKNOWN")
        self.assertFalse(c["shadow_policy_results"]["DEV_P95"])

    def test_stale_previous_window_snapshot_rejected(self):
        m,s,q,b=fixture(); s.seconds_remaining=10
        with self.assertRaisesRegex(ValueError,"current window"):
            build_observation(run_id="r",snapshot=s,mapping=m,quote=q,empirical_bounds=b)

    def test_quote_and_fee_metadata_are_shadow_only(self):
        m,s,q,b=fixture(); o,_=build_observation(run_id="r",snapshot=s,mapping=m,quote=q,empirical_bounds=b)
        self.assertEqual(o["quote"]["provider"],"KALSHI_PUBLIC_REST")
        self.assertEqual(o["quote"]["yes_ask"],Decimal(".72")); self.assertIsNone(o["fee_metadata"]["fee_type"])

    def test_append_immutable_and_restart_dedupe(self):
        m,s,q,b=fixture(); o,c=build_observation(run_id="r",snapshot=s,mapping=m,quote=q,empirical_bounds=b)
        with temporary() as d:
            store=ShadowStore(Path(d)); self.assertTrue(store.append("observations",o)); self.assertFalse(store.append("observations",o))
            store=ShadowStore(Path(d)); self.assertFalse(store.append("observations",o)); self.assertEqual(store.read("observations")[0]["kalshi_target"],"100.00")

    def test_partial_final_jsonl_line_is_ignored(self):
        m,s,q,b=fixture(); o,_=build_observation(run_id="r",snapshot=s,mapping=m,quote=q,empirical_bounds=b)
        with temporary() as d:
            store=ShadowStore(Path(d)); store.append("observations",o)
            with store.path("observations").open("a",encoding="utf-8") as handle: handle.write('{"partial"')
            self.assertEqual(len(ShadowStore(Path(d)).read("observations")),1)

    def test_resolution_separate_and_greater_equal(self):
        m,s,q,b=fixture(); o,_=build_observation(run_id="r",snapshot=s,mapping=m,quote=q,empirical_bounds=b)
        r=build_resolution(run_id="r",observation=o,market={"result":"yes","expiration_value":"100.00","floor_strike":"100.00","status":"settled"},resolved_at=m.window_end_utc)
        self.assertEqual(r["resolution_status"],"VERIFIED"); self.assertEqual(r["comparison_operator"],">=")
        self.assertNotIn("result",o)

    def test_missing_resolution_fails_closed(self):
        m,s,q,b=fixture(); o,_=build_observation(run_id="r",snapshot=s,mapping=m,quote=q,empirical_bounds=b)
        with self.assertRaises(ValueError): build_resolution(run_id="r",observation=o,market={"result":"","expiration_value":"100","floor_strike":"100"},resolved_at=m.window_end_utc)

    def test_audit_detects_forbidden_actionability(self):
        m,s,q,b=fixture(); o,_=build_observation(run_id="r",snapshot=s,mapping=m,quote=q,empirical_bounds=b)
        with temporary() as d:
            store=ShadowStore(Path(d)); store.append("observations",o)
            self.assertEqual(audit_store(store,now=m.window_end_utc)["status"],"PASS")
            path=store.path("observations"); row=json.loads(path.read_text()); row["actionability"]="ENTER"
            path.write_text(json.dumps(row)+"\n",encoding="utf-8")
            self.assertEqual(audit_store(ShadowStore(Path(d)),now=m.window_end_utc)["status"],"FAIL")

    def test_report_keeps_policies_separate(self):
        with temporary() as d:
            report=shadow_report(ShadowStore(Path(d)))
            self.assertEqual(set(report["policies"]),set(SHADOW_POLICIES)); self.assertEqual(report["next_milestone"],50)

    def test_250_window_simulation_no_actionability(self):
        with temporary() as d:
            result=simulate_shadow(Path(d),250)
            self.assertEqual(result["asset_attempts"],1000); self.assertEqual(result["observations"],1000)
            self.assertEqual(result["resolutions"],1000); self.assertEqual(result["audit"]["status"],"PASS")
            self.assertEqual(result["actionable_selections"],0); self.assertEqual(result["actionable_voice_events"],0)
            self.assertEqual(result["orders_submitted"],0)

    def test_frozen_step8_bounds_identity(self):
        with temporary() as d:
            root=Path(d); (root/"data").mkdir(); (root/"data"/"kalshi_step8_reference_risk_summary.json").write_text(json.dumps({
                "policy_version":REFERENCE_POLICY,"production_authorization":False,
                "bounds_fitted_on_development_only":{a:{"P50":2,"P95":7,"P99":12} for a in ASSETS}}),encoding="utf-8")
            self.assertEqual(load_empirical_bounds(root)["BTC-USD"]["P95"],Decimal("7"))

    def test_source_has_no_live_actionability_dependencies(self):
        package=Path(__file__).parents[1]/"mantis_v4"/"kalshi_forward"
        text="\n".join(p.read_text(encoding="utf-8") for p in package.glob("*.py"))
        self.assertNotIn("EventBus",text); self.assertNotIn("AlertRouter",text)
        self.assertNotIn("select_primary",text); self.assertNotIn("submit_order",text)


if __name__ == "__main__": unittest.main()
