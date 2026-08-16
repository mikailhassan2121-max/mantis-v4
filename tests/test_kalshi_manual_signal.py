from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from mantis_v4.economics.kalshi import KalshiEventQuote, KalshiMarketMapping
from mantis_v4.manual_signal.core import (ASSETS, FEE_MODEL_VERSION, POLICY_VERSION,
    MAX_TOTAL_ENTRY_COST, MIN_CONSERVATIVE_EDGE,
    FeeMetadata, FeeMetadataVerifier, KalshiFeeModel, ManualSignalStore, evaluate_candidate,
    apply_signal_lock, initial_manual_selection, select_primary_signal)
from mantis_v4.ui.voice import phrase_experimental_manual_signal
from mantis_v4.manual_signal.live import _placeholder
from mantis_v4.ui import CommandCenterState, PresentationConfig
from mantis_v4.ui.webmodel import snapshot_payload
from mantis_v4_live import build_parser


NOW=datetime(2026,8,16,3,57,tzinfo=UTC)
START=datetime(2026,8,16,3,45,tzinfo=UTC)
END=datetime(2026,8,16,4,0,tzinfo=UTC)
SERIES={"BTC-USD":"KXBTC15M","ETH-USD":"KXETH15M","SOL-USD":"KXSOL15M","XRP-USD":"KXXRP15M"}


def mapping(asset="BTC-USD"):
    series=SERIES[asset]
    return KalshiMarketMapping(asset,series,series+"-26AUG160400",series+"-26AUG160400-T100",
        "Crypto 15m","active",START,END,END,END,"greater",Decimal("100"),
        "CF_BENCHMARKS","KALSHI_CRYPTO15M_CF_BENCHMARKS","binary",True,NOW)


def quote(asset="BTC-USD", *, yes=Decimal("0.70"), no=Decimal("0.31"), age=Decimal("0")):
    m=mapping(asset); stamp=NOW-timedelta(seconds=float(age))
    return KalshiEventQuote(asset,m.market_ticker,m.event_ticker,m.series_ticker,
        Decimal("0.69"),Decimal("12"),Decimal("0.30"),Decimal("8"),yes,Decimal("8"),no,Decimal("12"),
        stamp,"LOCAL_RECEIVED_AT_NO_EXCHANGE_TIMESTAMP",NOW,age,"active","KALSHI_PUBLIC_REST",
        False,True,True,"CF_BENCHMARKS","KALSHI_CRYPTO15M_CF_BENCHMARKS",m.target,START,END)


def snapshot(asset="BTC-USD", side="YES", *, seconds=180, price=101.0, p=.97, lcb=.94):
    return SimpleNamespace(asset=asset,p_yes=p if side=="YES" else 1-p,p_no=1-p if side=="YES" else p,
        predicted_side=side,conservative_bound=lcb,fragility=10.0,disagreement=.01,
        crossing_probability=.10,reference_crossings=1,seconds_remaining=seconds,current_price=price)


def fees(asset="BTC-USD", verified=True):
    return FeeMetadata(SERIES[asset],"quadratic",Decimal("1"),"2026-07-07","OFFICIAL",NOW,verified,())


def candidate(asset="BTC-USD", side="YES", **changes):
    row=evaluate_candidate(snapshot=snapshot(asset,side),mapping=mapping(asset),quote=quote(asset),
        quote_status="READY",dev_p95_bps=Decimal({"BTC-USD":"5.47","ETH-USD":"7.03","SOL-USD":"7.58","XRP-USD":"6.52"}[asset]),
        fee_metadata=fees(asset),now=NOW)
    row.update(changes); return row


class KalshiManualSignalTests(unittest.TestCase):
    def test_exact_universe_and_cli(self):
        self.assertEqual(ASSETS,("BTC-USD","ETH-USD","SOL-USD","XRP-USD"))
        self.assertNotIn("ADA-USD",ASSETS)
        args=build_parser().parse_args(["--kalshi-manual-signals"])
        self.assertTrue(args.kalshi_manual_signals)
        self.assertTrue(build_parser().parse_args(["--kalshi-manual-diagnostics"]).kalshi_manual_diagnostics)
        self.assertEqual(_placeholder("BTC-USD","MARKET INITIALIZING","INITIALIZING",42)["seconds_remaining"],42)

    def test_manual_state_is_authoritative_before_first_scan(self):
        selection=initial_manual_selection(); op=selection["operator_state"]
        self.assertEqual(op["mode"],"KALSHI_MANUAL_SIGNAL")
        self.assertEqual(op["selection_policy_version"],POLICY_VERSION)
        self.assertEqual(op["actionability"],"EXPERIMENTAL_MANUAL_SIGNAL_ONLY")
        self.assertEqual(op["headline"],"SCANNING")
        self.assertFalse(op["first_scan_complete"])
        self.assertEqual([r["asset"] for r in op["candidate_rankings"]],list(ASSETS))
        self.assertNotIn("PRIMARY_SELECTOR_V1",str(selection))
        self.assertNotIn("WEBULL_OFFICIAL_FEE_SCHEDULE",str(selection))

    def test_backend_snapshot_carries_manual_state_to_web(self):
        state=CommandCenterState(list(ASSETS),PresentationConfig())
        state.set_primary_selection(initial_manual_selection())
        payload=snapshot_payload(state.snapshot(),PresentationConfig(),now=NOW)
        self.assertEqual(payload["operator_state"]["mode"],"KALSHI_MANUAL_SIGNAL")
        self.assertEqual(payload["operator_state"]["event_market_provider"],"KALSHI_PUBLIC_REST")
        self.assertEqual(len(payload["operator_state"]["candidate_rankings"]),4)

    def test_actual_target_and_dev_p95_gate(self):
        row=candidate()
        self.assertEqual(row["target"],"100")
        self.assertEqual(row["reference_policy"],"KALSHI_REFERENCE_RISK_V1_SHADOW / DEV_P95")
        close=candidate(proxy_current="100") if False else evaluate_candidate(snapshot=snapshot(price=100.02),
            mapping=mapping(),quote=quote(),quote_status="READY",dev_p95_bps=Decimal("5.47"),fee_metadata=fees(),now=NOW)
        self.assertEqual(close["status"],"REF AMBIGUOUS")

    def test_v2_can_signal_before_t300_and_populates_quotes(self):
        rows=[evaluate_candidate(snapshot=snapshot(a,seconds=873),mapping=mapping(a),quote=quote(a),
            quote_status="READY",dev_p95_bps=Decimal("10"),fee_metadata=fees(a),now=NOW) for a in ASSETS]
        selected=select_primary_signal(rows); op=selected["operator_state"]
        self.assertEqual(op["mode"],"KALSHI_MANUAL_SIGNAL")
        self.assertEqual(op["headline"],"PRIMARY SIGNAL")
        self.assertEqual(op["reason"],"ALL EXPERIMENTAL GATES PASSED")
        self.assertIsNone(op["seconds_until_entry_eligible"])
        self.assertTrue(op["first_scan_complete"])
        self.assertEqual(len(op["candidate_rankings"]),4)
        self.assertEqual(sum(r["status"]=="PRIMARY" for r in op["candidate_rankings"]),1)
        self.assertTrue(all(r["event_market_provider"]=="KALSHI_PUBLIC_REST" for r in rows))
        self.assertTrue(all(r["target"] and r["yes_ask"] and r["no_ask"] for r in rows))
        self.assertTrue(all(r["fee_provenance"]==fees(r["asset"]).fee_provenance for r in rows))
        self.assertTrue(all(r.get("fee") for r in rows))
        self.assertNotIn("WEBULL",str(op).upper())

    def test_yes_and_no_use_correct_asks(self):
        self.assertEqual(candidate(side="YES")["ask"],"0.70")
        self.assertEqual(candidate(side="NO")["ask"],"0.31")

    def test_stale_and_one_sided_fail_closed(self):
        stale=evaluate_candidate(snapshot=snapshot(),mapping=mapping(),quote=quote(age=Decimal("6")),
            quote_status="READY",dev_p95_bps=Decimal("5.47"),fee_metadata=fees(),now=NOW)
        self.assertEqual(stale["status"],"QUOTE STALE")
        one=replace(quote(),yes_ask=None,yes_ask_size=None)
        missing=evaluate_candidate(snapshot=snapshot(),mapping=mapping(),quote=one,quote_status="READY",
            dev_p95_bps=Decimal("5.47"),fee_metadata=fees(),now=NOW)
        self.assertEqual(missing["status"],"QUOTE UNAVAILABLE")

    def test_official_quadratic_fee_and_exact_rounding(self):
        model=KalshiFeeModel(); result=model.assess(Decimal("0.50"),fees())
        self.assertEqual(result.trade_fee,Decimal("0.0175"))
        self.assertEqual(result.rounding_fee,Decimal("0.0025"))
        self.assertEqual(result.total_fee,Decimal("0.0200"))
        self.assertEqual(result.total_cost,Decimal("0.52"))
        self.assertEqual(result.fee_model,FEE_MODEL_VERSION)

    def test_public_series_fee_metadata_verified_without_auth(self):
        class Client:
            def __init__(self): self.paths=[]
            def get(self,path,**params):
                self.paths.append((path,params))
                if path.startswith("/series/") and path!="/series/fee_changes":
                    ticker=path.rsplit("/",1)[-1]
                    return {"series":{"ticker":ticker,"frequency":"fifteen_min",
                                      "fee_type":"quadratic","fee_multiplier":1}}
                return {"series_fee_change_arr":[]}
        client=Client(); meta=FeeMetadataVerifier(client,now=lambda:NOW).verify("BTC-USD")
        self.assertTrue(meta.verified); self.assertEqual(meta.fee_type,"quadratic")
        self.assertEqual(meta.fee_multiplier,Decimal("1"))
        self.assertEqual(meta.fee_schedule_effective_date,"2026-07-07")
        self.assertEqual([p for p,_ in client.paths],["/series/KXBTC15M","/series/fee_changes"])

    def test_fee_unverified_and_economically_impossible(self):
        bad=evaluate_candidate(snapshot=snapshot(),mapping=mapping(),quote=quote(),quote_status="READY",
            dev_p95_bps=Decimal("5.47"),fee_metadata=fees(verified=False),now=NOW)
        self.assertEqual(bad["status"],"FEE UNVERIFIED")
        costly=evaluate_candidate(snapshot=snapshot(),mapping=mapping(),quote=quote(yes=Decimal("0.998")),
            quote_status="READY",dev_p95_bps=Decimal("5.47"),fee_metadata=fees(),now=NOW)
        self.assertEqual(costly["status"],"ECON FAIL")
        self.assertEqual(costly["reason"],"ECONOMICALLY IMPOSSIBLE")

    def test_v2_timing_and_operator_economic_guards(self):
        for seconds in (850,500,100):
            row=evaluate_candidate(snapshot=snapshot(seconds=seconds),mapping=mapping(),quote=quote(),
                quote_status="READY",dev_p95_bps=Decimal("5.47"),fee_metadata=fees(),now=NOW)
            self.assertEqual(row["status"],"ELIGIBLE",seconds)
        expensive=evaluate_candidate(snapshot=snapshot(p=.999,lcb=.99),mapping=mapping(),
            quote=quote(yes=Decimal("0.98")),quote_status="READY",dev_p95_bps=Decimal("5.47"),
            fee_metadata=fees(),now=NOW)
        self.assertEqual(expensive["status"],"EXPENSIVE CONTRACT")
        self.assertEqual(Decimal(expensive["max_total_entry_cost"]),MAX_TOTAL_ENTRY_COST)
        thin=evaluate_candidate(snapshot=snapshot(p=.97,lcb=.94),mapping=mapping(),
            quote=quote(yes=Decimal("0.92")),quote_status="READY",dev_p95_bps=Decimal("5.47"),
            fee_metadata=fees(),now=NOW)
        self.assertEqual(thin["status"],"ECON FAIL")
        self.assertEqual(Decimal(thin["min_conservative_edge"]),MIN_CONSERVATIVE_EDGE)

    def test_signal_lock_cannot_switch_or_disappear_and_resets_by_caller(self):
        first=select_primary_signal([candidate(a) for a in ASSETS])
        locked_state,locked=apply_signal_lock(first,None,NOW)
        self.assertEqual(locked_state["operator_state"]["headline"],"LOCKED PRIMARY SIGNAL")
        identity=(locked["asset"],locked["side"])
        changed=[]
        for asset in ASSETS:
            row=candidate(asset,"NO" if asset==locked["asset"] else "YES")
            row.update(status="CONF FAIL",economically_valid=False,confidence=.999)
            changed.append(row)
        later,still_locked=apply_signal_lock(select_primary_signal(changed),locked,NOW+timedelta(seconds=30))
        self.assertEqual((still_locked["asset"],still_locked["side"]),identity)
        self.assertEqual((later["selected"]["asset"],later["selected"]["side"]),identity)
        self.assertFalse(any(row["status"]=="PRIMARY" for row in later["candidates"]))
        self.assertTrue(later["selected"]["no_exit_signal"])
        self.assertNotIn("SELL",str(later).upper()); self.assertNotIn("EXIT SIGNAL",str(later).upper().replace("NO EXIT SIGNAL",""))

    def test_economics_and_selector_at_most_one(self):
        rows=[candidate(a) for a in ASSETS]
        result=select_primary_signal(rows)
        self.assertEqual(sum(r["status"]=="PRIMARY" for r in result["candidates"]),1)
        self.assertGreater(Decimal(result["selected"]["net_ev"]),0)
        self.assertGreater(Decimal(result["selected"]["conservative_net_ev"]),0)
        self.assertEqual(len(result["operator_state"]["candidate_rankings"]),4)
        no=select_primary_signal([{**r,"status":"CONF FAIL","economically_valid":False} for r in rows])
        self.assertIsNone(no["selected"])

    def test_signal_expires_when_quote_becomes_stale(self):
        live=candidate(); self.assertEqual(live["status"],"ELIGIBLE")
        stale=evaluate_candidate(snapshot=snapshot(),mapping=mapping(),quote=quote(age=Decimal("6")),
            quote_status="READY",dev_p95_bps=Decimal("5.47"),fee_metadata=fees(),now=NOW)
        rows=[stale]+[{**candidate(a),"status":"CONF FAIL","economically_valid":False} for a in ASSETS[1:]]
        self.assertIsNone(select_primary_signal(rows)["selected"])

    def test_ranking_uses_conservative_edge_before_probability(self):
        rows=[candidate(a) for a in ASSETS]
        for row in rows: row.update(conservative_edge="0.005",model_edge="0.006")
        rows[0].update(conservative_edge="0.01",model_edge="0.20",confidence=.999)
        rows[1].update(conservative_edge="0.02",model_edge="0.03",confidence=.96)
        self.assertEqual(select_primary_signal(rows)["selected"]["asset"],"ETH-USD")

    def test_append_only_dedupe_and_manual_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=ManualSignalStore(Path(tmp)); selection=select_primary_signal([candidate(a) for a in ASSETS])
            selected=apply_signal_lock(selection,None,NOW)[0]["selected"]
            self.assertTrue(store.append(selected,NOW)); self.assertFalse(store.append(selected,NOW))
            text=(Path(tmp)/"signals.jsonl").read_text()
            self.assertIn('"manual_only":true',text); self.assertIn('"order_capability":"DISABLED"',text)
            self.assertNotIn("order_id",text)

    def test_voice_is_manual_only_without_buy_or_enter(self):
        line=phrase_experimental_manual_signal("BTC-USD","YES","0.72")
        self.assertIn("experimental signal",line); self.assertIn("Manual entry only",line)
        self.assertNotIn("Buy",line); self.assertNotIn("Enter",line)

    def test_ui_contains_experimental_manual_contract(self):
        text=Path("mantis_v4/ui/web/modules.js").read_text(encoding="utf-8")
        for label in ("KALSHI EXPERIMENTAL MANUAL SIGNAL","EXPERIMENTAL — NOT YET FORWARD VALIDATED",
                      "MANUAL ONLY","REF RISK / ASK","FEE / CONS EDGE"):
            self.assertIn(label,text)
        self.assertIn('operator.mode === "KALSHI_MANUAL_SIGNAL"',text)

    def test_no_auth_order_or_webull_in_manual_package(self):
        text="\n".join(p.read_text(encoding="utf-8") for p in Path("mantis_v4/manual_signal").glob("*.py"))
        self.assertNotIn("Webull",text); self.assertNotIn("api_key",text)
        self.assertNotIn("place_order",text); self.assertNotIn("submit_order",text)
        self.assertIn('"authentication":"NONE"',text)


if __name__=="__main__": unittest.main()
