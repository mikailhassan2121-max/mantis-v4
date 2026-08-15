import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from mantis_v4.config import MantisConfig
from mantis_v4.economics.fees import (WEBULL_EVENT_FEE_PROVENANCE,
    WEBULL_EVENT_OPENING_FEE)
from mantis_v4.forward import EventBus, EventType, ForwardStore
from mantis_v4.forward.events import AppEvent
from mantis_v4.selection import LIVE_ASSETS, SELECTION_POLICY_ID, evaluate, select_primary
from mantis_v4.ui.alerts import AlertRouter
from mantis_v4.ui.demo import DemoFeed
from mantis_v4.ui.settings import PresentationConfig
from mantis_v4.ui.state import CommandCenterState
from mantis_v4.ui.webmodel import snapshot_payload

UTC=timezone.utc

def snap(asset="BTC-USD", side="YES", probability=.96, lcb=.92, yes_ask=.70, no_ask=.30,
         final="ENTER YES", phase6=None, reason="ROBUST_POSITIVE_EV", fragility=20):
    pyes=probability if side=="YES" else 1-probability
    return SimpleNamespace(asset=asset,predicted_side=side,p_yes=pyes,p_no=1-pyes,
      conservative_bound=lcb,fragility=fragility,disagreement=.01,crossing_probability=.10,
      reference_crossings=1,contract_id="2026-08-15T12:00:00+00:00",
      phase6_state=phase6 or f"ENTER {side}",phase6_reason="ELIGIBLE",
      final_decision=final,final_reason=reason,yes_ask=yes_ask,no_ask=no_ask,
      metadata={"provider_mode":"webull-official"})

def scanned(asset="BTC-USD", *, quote=False, seconds=600):
    value=snap(asset,yes_ask=.70 if quote else None)
    value.timestamp_utc="2026-08-15T16:05:00+00:00"
    value.window_start="2026-08-15T16:00:00+00:00"
    value.window_end="2026-08-15T16:15:00+00:00"
    value.seconds_remaining=seconds
    value.reference=100.0; value.current_price=101.0; value.buffer=1.0
    value.reference_status="PROXY"; value.volatility_estimate=.01
    value.yes_bid=None; value.no_bid=None; value.quote_status="UNAVAILABLE"
    value.quote_age=None; value.break_even=None; value.model_edge=None; value.lcb_edge=None
    value.point_ev=None; value.lcb_ev=None; value.expected_return_on_cost=None
    value.ev_status="ECONOMICS_UNAVAILABLE"; value.fees_status="VERIFIED_FEES"
    value.slippage_status="SLIPPAGE_NOT_MODELED"; value.data_age_seconds=0
    value.fetch_latency_seconds=.1
    return value

class SelectorTests(unittest.TestCase):
    def test_default_live_universe_is_exactly_four(self):
        self.assertEqual(LIVE_ASSETS,("BTC-USD","ETH-USD","SOL-USD","XRP-USD"))
        self.assertEqual(tuple(MantisConfig().active_assets),LIVE_ASSETS)
        self.assertNotIn("ADA-USD",LIVE_ASSETS)

    def test_four_candidates_and_at_most_one_selection(self):
        result=select_primary([snap(a) for a in LIVE_ASSETS])
        self.assertEqual(len(result["candidates"]),4)
        self.assertEqual(sum(c["status"]=="ACTIONABLE" for c in result["candidates"]),4)
        self.assertIsNotNone(result["selected"]); self.assertEqual(result["selection_policy"],SELECTION_POLICY_ID)

    def test_economics_outrank_raw_probability(self):
        result=select_primary([snap("BTC-USD",probability=.99,lcb=.95,yes_ask=.94),
          snap("ETH-USD",probability=.97,lcb=.94,yes_ask=.70),snap("SOL-USD",phase6="WAIT"),snap("XRP-USD",phase6="WAIT")])
        self.assertEqual(result["selected"]["asset"],"ETH-USD")

    def test_negative_point_and_conservative_ev_reject(self):
        point=evaluate(snap(probability=.96,lcb=.92,yes_ask=.95))
        conservative=evaluate(snap(probability=.96,lcb=.92,yes_ask=.91))
        self.assertEqual(point.reason,"NEGATIVE_NET_EV")
        self.assertEqual(conservative.reason,"CONSERVATIVE_EV_LE_ZERO")

    def test_impossible_998_contract_rejected(self):
        candidate=evaluate(snap(probability=.999,lcb=.999,yes_ask=.998))
        self.assertEqual(candidate.reason,"ECONOMICALLY_IMPOSSIBLE")
        self.assertGreaterEqual(candidate.ask+candidate.fee,1.0)

    def test_side_uses_correct_executable_ask(self):
        self.assertEqual(evaluate(snap(side="YES",yes_ask=.71,no_ask=.22)).ask,.71)
        self.assertEqual(evaluate(snap(side="NO",yes_ask=.71,no_ask=.22,final="ENTER NO")).ask,.22)

    def test_missing_quote_cannot_select(self):
        candidate=evaluate(snap(yes_ask=None))
        self.assertEqual((candidate.status,candidate.reason),("ECONOMICS UNVERIFIED","QUOTE_UNAVAILABLE"))

    def test_fee_and_quote_provenance(self):
        candidate=evaluate(snap())
        self.assertEqual(candidate.fee,float(WEBULL_EVENT_OPENING_FEE))
        self.assertEqual(candidate.fee_provenance,WEBULL_EVENT_FEE_PROVENANCE)
        self.assertEqual(candidate.quote_provenance,"webull-official")

    def test_ranking_is_deterministic(self):
        values=[snap(a) for a in reversed(LIVE_ASSETS)]
        self.assertEqual(select_primary(values)["selected"]["asset"],"BTC-USD")

    def test_historical_ada_remains_readable_and_primary_is_append_only(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            store=ForwardStore(Path(tmp)); store.append("observations",{"observation_id":"ada","asset":"ADA-USD","contract_id":"old"},"observation_id")
            row={"selection_id":"one","contract_id":"window","selection_policy":SELECTION_POLICY_ID}
            self.assertTrue(store.append("primary_selections",row,"selection_id")); self.assertFalse(store.append("primary_selections",row,"selection_id"))
            self.assertEqual(store.read("observations")[0]["asset"],"ADA-USD")

class SelectionPresentationTests(unittest.TestCase):
    def setUp(self):
        self.config=PresentationConfig(boot_sequence_enabled=False,audio_enabled=True,voice_enabled=True)

    def test_authoritative_selection_reaches_snapshot_json(self):
        state=CommandCenterState(LIVE_ASSETS,self.config)
        selection=select_primary([snap(a) for a in LIVE_ASSETS]); state.set_primary_selection(selection,persisted=True)
        payload=snapshot_payload(state.snapshot(),self.config)
        self.assertEqual(payload["primary_selection"]["selected"]["asset"],"BTC-USD")
        self.assertEqual(len(payload["primary_selection"]["candidates"]),4)

    def test_voice_only_primary_and_deduplicates(self):
        class Audio:
            def play(self,*a): pass
        class Voice:
            def __init__(self): self.lines=[]
            def say(self,line): self.lines.append(line)
        voice=Voice(); state=CommandCenterState(LIVE_ASSETS,self.config); bus=EventBus()
        router=AlertRouter(state,Audio(),voice,self.config).attach(bus); router.arm(announce=False)
        qualified=AppEvent(EventType.ENTRY_YES,"now",{"asset":"ETH-USD","side":"YES"})
        selection=select_primary([snap(a) for a in LIVE_ASSETS])
        state.set_primary_selection(selection,persisted=True)
        primary=AppEvent(EventType.PRIMARY_SELECTION,"now",selection["selected"])
        bus.emit(qualified); bus.emit(primary); bus.emit(primary)
        self.assertEqual(len(voice.lines),1); self.assertIn("Primary selection",voice.lines[0])

    def test_first_valid_scan_leaves_standby_and_has_four_rows(self):
        state=CommandCenterState(LIVE_ASSETS,self.config)
        state.update_from_snapshot(scanned())
        operator=snapshot_payload(state.snapshot(),self.config)["operator_state"]
        self.assertEqual(operator["mode"],"SCANNING")
        self.assertEqual(len(operator["candidate_rankings"]),4)
        self.assertEqual([c["asset"] for c in operator["candidate_rankings"]],list(LIVE_ASSETS))

    def test_quote_unavailable_is_no_trade_and_cannot_speak(self):
        class Voice:
            def __init__(self): self.lines=[]
            def say(self,line): self.lines.append(line)
        class Audio:
            def play(self,*args): pass
        snapshots=[scanned(a) for a in LIVE_ASSETS]
        selection=select_primary(snapshots)
        state=CommandCenterState(LIVE_ASSETS,self.config)
        for value in snapshots: state.update_from_snapshot(value)
        state.set_primary_selection(selection,persisted=False)
        voice=Voice(); router=AlertRouter(state,Audio(),voice,self.config); router.arm(announce=False)
        router.handle(AppEvent(EventType.ENTRY_YES,"now",{"asset":"BTC-USD","side":"YES"}))
        router.handle(AppEvent(EventType.PRIMARY_SELECTION,"now",evaluate(snap()).payload()))
        operator=snapshot_payload(state.snapshot(),self.config)["operator_state"]
        self.assertEqual(operator["mode"],"SCANNING")
        self.assertIsNone(operator["primary_selection"])
        self.assertEqual(voice.lines,[])
        self.assertEqual(state.snapshot().status.actionable_voice_events,0)
        self.assertEqual(state.snapshot().status.voice_events_suppressed,2)

    def test_quote_unavailable_inside_entry_window_is_no_trade(self):
        snapshots=[scanned(a,seconds=240) for a in LIVE_ASSETS]
        selection=select_primary(snapshots)
        self.assertEqual(selection["operator_state"]["mode"],"NO_TRADE")
        self.assertEqual(selection["operator_state"]["reason"],"QUOTE_UNAVAILABLE")

    def test_both_legacy_entry_events_are_never_spoken(self):
        class Voice:
            def __init__(self): self.lines=[]
            def say(self,line): self.lines.append(line)
        class Audio:
            def play(self,*args): pass
        state=CommandCenterState(LIVE_ASSETS,self.config); voice=Voice()
        router=AlertRouter(state,Audio(),voice,self.config); router.arm(announce=False)
        router.handle(AppEvent(EventType.ENTRY_YES,"now",{"asset":"BTC-USD","side":"YES"}))
        router.handle(AppEvent(EventType.ENTRY_NO,"now",{"asset":"ETH-USD","side":"NO"}))
        self.assertEqual(voice.lines,[])
        self.assertEqual(state.snapshot().status.actionable_voice_events,0)

    def test_provider_failure_is_visible_not_standby(self):
        state=CommandCenterState(LIVE_ASSETS,self.config)
        state.mark_no_data("BTC-USD","UNDERLYING_DATA_UNAVAILABLE")
        operator=snapshot_payload(state.snapshot(),self.config)["operator_state"]
        self.assertEqual((operator["mode"],operator["headline"]),("PROVIDER_DEGRADED","DATA HOLD"))

    def test_center_ui_has_primary_no_trade_and_four_candidate_table(self):
        root=Path(__file__).parents[1]/"mantis_v4/ui/web"
        js=(root/"modules.js").read_text(encoding="utf-8")
        html=(root/"index.html").read_text(encoding="utf-8")
        for token in ("PRIMARY CONTRACT SELECTION","STRONGEST CURRENT CANDIDATE","NO TRADE","WAIT / NO TRADE"):
            self.assertIn(token,js)
        self.assertIn("snap.operator_state",js)
        self.assertNotIn('if (key === "enter_yes") A.play', (root/"app.js").read_text(encoding="utf-8"))
        self.assertIn('id="candidate_ranking"',html)
        self.assertIn("FOUR-CANDIDATE SCAN",html)

    def test_demo_selector_is_four_asset_and_store_free(self):
        snapshots=DemoFeed(LIVE_ASSETS).snapshots(datetime(2026,8,15,12,11,tzinfo=UTC))
        result=select_primary(snapshots)
        self.assertEqual([c["asset"] for c in result["candidates"]],list(LIVE_ASSETS))
        self.assertNotIn("ADA-USD",str(result))

if __name__=="__main__": unittest.main()
