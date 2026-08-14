import hashlib,json,tempfile,unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from mantis_v4.clock import Instant
from mantis_v4.contracts import ContractWindow
from mantis_v4.economics import break_even_probability,expected_value
from mantis_v4.forward import *
UTC=timezone.utc; NOW=datetime(2026,8,14,18,11,tzinfo=UTC)
def state(**kw):
 w=ContractWindow.for_instant(Instant(NOW,0),ZoneInfo("America/New_York"),15)
 d=dict(asset="BTC-USD",contract_id=w.contract_id,window_start=w.start_utc,window_end=w.end_utc,scan_timestamp=NOW,reference=100.,reference_status=ReferenceStatus.PROXY,current_price=102.,volatility_estimate=.01,p_yes=.96,conservative_bound=.92,fragility=40.,disagreement=.01,crossing_probability=.1,reference_crossings=2)
 d.update(kw); return LiveAssetState(**d)
class BoundaryTests(unittest.TestCase):
 def test_exact_quarter_hour_boundaries(self):
  tz=ZoneInfo("America/New_York")
  for h,m in [(14,15),(14,30),(14,45),(15,0),(0,0)]:
   day=15 if (h,m)==(0,0) else 14; dt=datetime(2026,8,day,h,m,tzinfo=tz); w=ContractWindow.for_instant(Instant(dt.astimezone(UTC),0),tz,15)
   self.assertEqual(w.start_local.minute,m); self.assertEqual(w.start_local.hour,h)
 def test_second_before_boundary(self):
  tz=ZoneInfo("America/New_York"); before=datetime(2026,8,14,14,14,59,tzinfo=tz); after=before+timedelta(seconds=1)
  self.assertNotEqual(ContractWindow.for_instant(Instant(before.astimezone(UTC),0),tz,15),ContractWindow.for_instant(Instant(after.astimezone(UTC),1),tz,15))
class StoreEngineTests(unittest.TestCase):
 def setUp(self): self.tmp=tempfile.TemporaryDirectory(); self.store=ForwardStore(Path(self.tmp.name)); self.engine=ForwardEngine(self.store,run_id="run")
 def tearDown(self): self.tmp.cleanup()
 def test_one_entry_per_contract_and_restart(self):
  self.engine.process(state()); self.engine.process(state(scan_timestamp=NOW+timedelta(seconds=5)))
  self.assertEqual(len(self.store.read("entries")),1)
  ForwardEngine(ForwardStore(Path(self.tmp.name)),run_id="restart").process(state(scan_timestamp=NOW+timedelta(seconds=10)))
  self.assertEqual(len(self.store.read("entries")),1)
 def test_observation_immutable_after_resolution(self):
  self.engine.process(state()); original=self.store.path("observations").read_bytes()
  self.engine.resolve(asset="BTC-USD",contract_id=state().contract_id,resolution_timestamp=state().window_end,terminal_value=103,reference=100)
  self.assertEqual(original,self.store.path("observations").read_bytes())
 def test_no_lookahead(self):
  first=self.engine.process(state()); self.engine.process(state(scan_timestamp=NOW+timedelta(seconds=5),current_price=50,p_yes=.01,conservative_bound=.95))
  persisted=self.store.read("observations")[0]
  self.assertEqual(first.p_yes,persisted["p_yes"]); self.assertEqual(first.final_decision,persisted["final_decision"])
 def test_stale_underlying_data_hold(self): self.assertEqual(self.engine.process(state(data_age_seconds=91)).phase6_state,"DATA HOLD")
 def test_missing_economics_wait_but_classification_entry(self):
  snap=self.engine.process(state()); self.assertEqual(snap.final_reason,"CONTRACT_QUOTE_UNAVAILABLE"); self.assertEqual(len(self.store.read("entries")),1)
 def test_proxy_reference_is_prominent(self): self.assertEqual(self.engine.process(state()).reference_status,"PROXY_UNVERIFIED")
 def test_official_reference_status_preserved(self): self.assertEqual(self.engine.process(state(reference_status=ReferenceStatus.OFFICIAL)).reference_status,"OFFICIAL_VERIFIED_REFERENCE")
 def test_resolution_yes_and_no_correctness(self):
  self.engine.process(state()); yes=self.engine.resolve(asset="BTC-USD",contract_id=state().contract_id,resolution_timestamp=state().window_end,terminal_value=101,reference=100)
  self.assertTrue(yes["classification_correct"]); self.assertEqual(yes["resolution_verification_status"],"PROXY_RESOLUTION")
 def test_no_entry_resolution_has_no_correctness(self):
  x=self.engine.resolve(asset="ETH-USD",contract_id="none",resolution_timestamp=NOW,terminal_value=90,reference=100); self.assertIsNone(x["classification_correct"])
 def test_unresolved_recovery(self): self.engine.process(state()); self.assertEqual(len(self.store.unresolved_entries()),1)
 def test_rollover_event_and_state_reset(self):
  seen=[]; self.engine.events.subscribe(EventType.ROLLOVER,lambda e:seen.append(e)); self.engine.process(state())
  later=NOW+timedelta(minutes=15); w=ContractWindow.for_instant(Instant(later,0),ZoneInfo("America/New_York"),15); self.engine.process(state(contract_id=w.contract_id,window_start=w.start_utc,window_end=w.end_utc,scan_timestamp=later,reference_crossings=0)); self.assertEqual(len(seen),1)
 def test_utc_and_local_timestamps(self):
  snap=self.engine.process(state()); self.assertTrue(snap.timestamp_utc.endswith("+00:00")); self.assertIn("-04:00",snap.timestamp_local)
 def test_partial_tail_recovery(self):
  self.store.path("observations").write_text('{"observation_id":"ok"}\n{"bad"',encoding="utf-8"); recovered=ForwardStore(Path(self.tmp.name)); self.assertEqual(len(recovered.read("observations")),1)
 def test_malformed_middle_rejected(self):
  self.store.path("observations").write_text('{bad}\n{"observation_id":"ok"}\n',encoding="utf-8")
  with self.assertRaises(ValueError): ForwardStore(Path(self.tmp.name))
 def test_forward_report_and_export(self):
  self.engine.process(state()); self.engine.resolve(asset="BTC-USD",contract_id=state().contract_id,resolution_timestamp=state().window_end,terminal_value=101,reference=100)
  report=forward_report(self.store,n_boot=10); self.assertEqual(report["classification_accuracy"],1); self.assertEqual(report["sample_label"],"VERY SMALL SAMPLE")
  self.assertTrue(export_csv(self.store,Path(self.tmp.name)/"export"))
 def test_shift_sample_too_small(self): self.assertEqual(compare_historical({"resolved_entries":0},[])["status"],"FORWARD_SAMPLE_TOO_SMALL")
 def test_replay_harness_rejects_out_of_order(self):
  harness=ForwardReplayHarness(self.engine)
  with self.assertRaises(ValueError): harness.run([state(scan_timestamp=NOW+timedelta(seconds=5)),state()])
 def test_provider_health_append(self):
  row={"health_id":"h","timestamp_utc":NOW.isoformat(),"provider":"stub","successful_fetches":1,"failed_fetches":0}
  self.assertTrue(self.store.append("provider_health",row,"health_id")); self.assertFalse(self.store.append("provider_health",row,"health_id"))
class IntegrityTests(unittest.TestCase):
 def test_phase6_policy_unchanged(self):
  p=__import__('mantis_v4.economics',fromlist=['Phase7Config']).Phase7Config().policy().classification
  self.assertEqual((p.name,p.probability_threshold,p.lcb_threshold,p.max_fragility,p.max_disagreement,p.max_seconds_remaining,p.max_crossing_probability,p.max_crossings),("H_p0.95_l0.90_f50_d.05_t300",.95,.90,50,.05,300,.35,4))
 def test_phase7_formulas_unchanged(self): self.assertAlmostEqual(break_even_probability(.6,1,.02),.62); self.assertAlmostEqual(expected_value(.95,.6,1,.03),.32)
 def test_v3_unchanged(self):
  p=Path(__file__).resolve().parents[1]/"mantis_15m_resolution_v3.py"; self.assertEqual(hashlib.sha1(p.read_bytes()).hexdigest(),"cb7c65e3b00be5446c3bc403d7c043e39dc95eda")
if __name__=="__main__": unittest.main()
