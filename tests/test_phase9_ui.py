"""Phase 9 presentation tests.

Two jobs: prove the command center draws backend state faithfully, and prove it
cannot damage anything below it -- not the quant loop, not the forward logs,
and not the locked Phase 6/7/8 behaviour.
"""
import hashlib,inspect,tempfile,threading,time,unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from mantis_v4.clock import Instant
from mantis_v4.contracts import ContractWindow
from mantis_v4.economics import Phase7Config,break_even_probability,expected_value
from mantis_v4.entry.decision import Decision
from mantis_v4.forward import (EventBus,EventType,ForwardConfig,ForwardEngine,ForwardStore,
 LiveAssetState,LiveSnapshot,ReferenceStatus)
from mantis_v4.forward.events import AppEvent
from mantis_v4.ui import (AlertRouter,CommandCenter,CommandCenterState,PresentationConfig,
 Severity,build_audio,build_voice,render_to_text,view_from_snapshot)
from mantis_v4.ui import alerts as ui_alerts, audio as ui_audio, panels, theme, voice as ui_voice
from mantis_v4.ui.dashboard import build_console
from mantis_v4.ui.demo import DemoFeed,emit_demo_events
from mantis_v4.ui.state import AssetView

UTC=timezone.utc; NOW=datetime(2026,8,14,18,11,tzinfo=UTC)
ASSETS=["BTC-USD","ETH-USD","SOL-USD","XRP-USD","ADA-USD"]
REPO=Path(__file__).resolve().parents[1]


def config(**kw):
 cfg=PresentationConfig()
 for key,value in kw.items(): setattr(cfg,key,value)
 return cfg.validate()

def asset_state(**kw):
 w=ContractWindow.for_instant(Instant(NOW,0),ZoneInfo("America/New_York"),15)
 d=dict(asset="BTC-USD",contract_id=w.contract_id,window_start=w.start_utc,window_end=w.end_utc,
  scan_timestamp=NOW,reference=100.,reference_status=ReferenceStatus.PROXY,current_price=102.,
  volatility_estimate=.01,p_yes=.96,conservative_bound=.92,fragility=40.,disagreement=.01,
  crossing_probability=.1,reference_crossings=2)
 d.update(kw); return LiveAssetState(**d)

def snapshot(**kw):
 d=dict(run_id="r",observation_id="o",timestamp_utc=NOW.isoformat(),timestamp_local=NOW.isoformat(),
  asset="BTC-USD",contract_id="C1",window_start=NOW.isoformat(),
  window_end=(NOW+timedelta(minutes=5)).isoformat(),seconds_remaining=300.,reference=100.,
  reference_status="PROXY_UNVERIFIED",current_price=102.,buffer=2.,volatility_estimate=.01,
  p_yes=.964,p_no=.036,predicted_side="YES",conservative_bound=.921,fragility=41.2,
  disagreement=.012,crossing_probability=.08,reference_crossings=1,phase6_state="ENTER YES",
  phase6_reason="ELIGIBLE",final_decision="WAIT",final_reason="CONTRACT_QUOTE_UNAVAILABLE",
  data_age_seconds=4.,fetch_latency_seconds=.3,next_scan_utc=NOW.isoformat())
 d.update(kw); return LiveSnapshot(**d)


class FakeAudio:
 """Synchronous stand-in so alert routing tests never depend on thread timing."""
 def __init__(self): self.played=[]
 def play(self,cue): self.played.append(cue); return True
 def stop(self,timeout=0): pass
 def drain(self,timeout=0): pass
 @property
 def status(self): return ui_audio.AudioStatus(True,"fake")

class FakeVoice:
 def __init__(self): self.spoken=[]
 def say(self,text): self.spoken.append(text); return True
 def stop(self,timeout=0): pass
 def drain(self,timeout=0): pass
 @property
 def status(self): return ui_voice.VoiceStatus(True,"fake")


def router_with(**cfg_kw):
 cfg_kw.setdefault("startup_alert_suppression_seconds",0.0)
 cfg_kw.setdefault("boot_sequence_enabled",False)
 cfg=config(**cfg_kw); state=CommandCenterState(ASSETS,cfg)
 audio=FakeAudio(); voice=FakeVoice()
 return AlertRouter(state,audio,voice,cfg),state,audio,voice


# ---------------------------------------------------------------------------
# Event hook routing
# ---------------------------------------------------------------------------

class HookRoutingTests(unittest.TestCase):
 def test_every_hook_has_exactly_one_route(self):
  self.assertEqual(set(ui_alerts.ROUTES),set(EventType))

 def test_severity_mapping(self):
  expected={EventType.ROLLOVER:Severity.INFO,EventType.RESOLUTION:Severity.NOTICE,
   EventType.ENTRY_YES:Severity.ACTION,EventType.ENTRY_NO:Severity.ACTION,
   EventType.WAIT:Severity.INFO,EventType.DATA_HOLD:Severity.WARNING,
   EventType.ERROR:Severity.CRITICAL}
  for event_type,severity in expected.items():
   self.assertEqual(ui_alerts.ROUTES[event_type].severity,severity,event_type)

 def test_router_subscribes_to_the_engine_bus(self):
  router,state,audio,_=router_with()
  bus=EventBus(); router.attach(bus)
  bus.emit(AppEvent(EventType.ENTRY_YES,NOW.isoformat(),{"asset":"BTC-USD","side":"YES"}))
  self.assertEqual([e.message for e in state.snapshot().events],["QUALIFIED YES"])
  self.assertEqual(audio.played,["enter_yes"])

 def test_engine_entry_event_reaches_the_event_log(self):
  with tempfile.TemporaryDirectory() as tmp:
   bus=EventBus(); router,state,audio,voice=router_with(); router.attach(bus)
   engine=ForwardEngine(ForwardStore(Path(tmp)),events=bus,run_id="run")
   engine.process(asset_state())
   messages=[e.message for e in state.snapshot().events]
   self.assertIn("QUALIFIED YES",messages)
   self.assertEqual(audio.played,["enter_yes"])


class AlertBehaviourTests(unittest.TestCase):
 def test_startup_gate_logs_but_suppresses_operational_audio_and_voice(self):
  router,state,audio,voice=router_with(startup_alert_suppression_seconds=60.0)
  router.handle(AppEvent(EventType.ENTRY_YES,NOW.isoformat(),
   {"asset":"BTC-USD","side":"YES"}))
  self.assertEqual(state.snapshot().events[-1].message,"QUALIFIED YES")
  self.assertEqual(audio.played,[])
  self.assertEqual(voice.spoken,[])

 def test_enter_yes_alert(self):
  router,state,audio,voice=router_with()
  router.handle(AppEvent(EventType.ENTRY_YES,NOW.isoformat(),
   {"asset":"BTC-USD","side":"YES","seconds_remaining":142,"model_probability":.972}))
  self.assertEqual(audio.played,["enter_yes"])
  self.assertEqual(voice.spoken,[])
  entry=state.snapshot().events[-1]
  self.assertEqual((entry.severity,entry.message),("ACTION","QUALIFIED YES"))
  self.assertIn("T-142s",entry.detail)

 def test_enter_no_alert_is_distinct(self):
  router,_,audio,voice=router_with()
  router.handle(AppEvent(EventType.ENTRY_NO,NOW.isoformat(),{"asset":"ETH-USD","side":"NO"}))
  self.assertEqual(audio.played,["enter_no"])
  self.assertEqual(voice.spoken,[])
  self.assertNotEqual(ui_audio.CUES["enter_yes"],ui_audio.CUES["enter_no"])

 def test_wait_is_silent(self):
  router,state,audio,voice=router_with()
  router.handle(AppEvent(EventType.WAIT,NOW.isoformat(),
   {"asset":"BTC-USD","reason":"PROBABILITY_TOO_LOW"}))
  self.assertEqual(audio.played,[])
  self.assertEqual(voice.spoken,[])
  self.assertEqual(state.snapshot().events[-1].message,"WAIT")
  self.assertIsNone(ui_alerts.ROUTES[EventType.WAIT].audio_cue)

 def test_repeated_identical_wait_does_not_flood_the_log(self):
  router,state,_,_=router_with()
  for _ in range(5):
   router.handle(AppEvent(EventType.WAIT,NOW.isoformat(),
    {"asset":"BTC-USD","reason":"PROBABILITY_TOO_LOW"}))
  self.assertEqual(len(state.snapshot().events),1)
  router.handle(AppEvent(EventType.WAIT,NOW.isoformat(),
   {"asset":"BTC-USD","reason":"FRAGILITY_TOO_HIGH"}))
  self.assertEqual(len(state.snapshot().events),2)

 def test_data_hold_alert(self):
  router,state,audio,voice=router_with()
  router.handle(AppEvent(EventType.DATA_HOLD,NOW.isoformat(),
   {"asset":"BTC-USD","reason":"STALE_DATA"}))
  self.assertEqual(audio.played,["data_hold"])
  self.assertEqual(voice.spoken,["Data hold. Bitcoin market data stale."])
  self.assertEqual(state.snapshot().events[-1].severity,"WARNING")

 def test_rollover_alert_is_informational_and_unspoken_by_default(self):
  router,state,audio,voice=router_with()
  router.handle(AppEvent(EventType.ROLLOVER,NOW.isoformat(),{"to":"C2"}))
  self.assertEqual(audio.played,["rollover"])
  self.assertEqual(voice.spoken,[])          # voice_rollover defaults off
  self.assertEqual(state.snapshot().events[-1].severity,"INFO")

 def test_rollover_can_be_spoken_when_enabled(self):
  router,_,_,voice=router_with(voice_rollover=True)
  router.handle(AppEvent(EventType.ROLLOVER,NOW.isoformat(),{"asset":"BTC-USD","to":"C2"}))
  self.assertEqual(voice.spoken,["New Bitcoin contract window."])

 def test_resolution_alert_reports_correctness(self):
  router,state,audio,voice=router_with()
  router.handle(AppEvent(EventType.RESOLUTION,NOW.isoformat(),
   {"asset":"BTC-USD","winning_side":"YES","classification_correct":True,
    "resolution_verification_status":"PROXY_RESOLUTION"}))
  self.assertEqual(audio.played,["resolution"])
  self.assertEqual(voice.spoken,["Bitcoin contract resolved. Prediction correct."])
  self.assertEqual(state.snapshot().events[-1].severity,"NOTICE")

 def test_error_alert_is_critical(self):
  router,state,audio,voice=router_with()
  router.handle(AppEvent(EventType.ERROR,NOW.isoformat(),
   {"component":"market data","message":"boom"}))
  self.assertEqual(audio.played,["error"])
  self.assertEqual(state.snapshot().events[-1].severity,"CRITICAL")

 def test_rate_limit_suppresses_repeat_tones(self):
  router,_,audio,_=router_with(audio_min_interval_seconds=999.)
  for _ in range(3):
   router.handle(AppEvent(EventType.DATA_HOLD,NOW.isoformat(),
    {"asset":"BTC-USD","reason":f"R{_}"}))
  self.assertEqual(audio.played,["data_hold"])

 def test_provider_failure_notice(self):
  router,state,audio,_=router_with()
  router.provider_failure("yahoo","HTTP 503")
  self.assertEqual(audio.played,["provider_failure"])
  self.assertEqual(state.snapshot().events[-1].severity,"WARNING")


class AudioVoiceSubsystemTests(unittest.TestCase):
 def test_audio_disabled_plays_nothing(self):
  engine=build_audio(config(audio_enabled=False))
  self.assertFalse(engine.play("enter_yes"))
  self.assertEqual(engine.played,[])
  self.assertFalse(engine.status.available)

 def test_voice_disabled_says_nothing(self):
  engine=build_voice(config(voice_enabled=False))
  self.assertFalse(engine.say("MANTIS."))
  self.assertEqual(engine.spoken,[])
  self.assertFalse(engine.status.available)

 def test_per_event_audio_switch(self):
  cfg=config(audio_enter_yes=False)
  self.assertFalse(cfg.audio_allows("enter_yes"))
  self.assertTrue(cfg.audio_allows("enter_no"))
  router,_,audio,_=router_with(audio_enter_yes=False)
  router.handle(AppEvent(EventType.ENTRY_YES,NOW.isoformat(),{"asset":"BTC-USD","side":"YES"}))
  self.assertEqual(audio.played,[])

 def test_master_volume_zero_is_silent(self):
  engine=build_audio(config(master_volume=0.))
  try: self.assertFalse(engine.play("enter_yes"))
  finally: engine.stop()

 def test_audio_plays_through_an_injected_device(self):
  calls=[]
  engine=ui_audio.AudioEngine(config(),backend=lambda f,d:calls.append((f,d)))
  try:
   self.assertTrue(engine.play("enter_yes")); engine.drain(3.)
   time.sleep(.15)
   self.assertEqual(len(calls),len(ui_audio.CUES["enter_yes"]))
  finally: engine.stop()

 def test_audio_device_failure_degrades_instead_of_raising(self):
  def broken(f,d): raise OSError("no audio device")
  engine=ui_audio.AudioEngine(config(),backend=broken)
  try:
   for _ in range(4): engine.play("enter_yes")
   engine.drain(3.); time.sleep(.3)
   self.assertFalse(engine.status.available)
   self.assertFalse(engine.play("enter_yes"))   # still returns, never raises
  finally: engine.stop()

 def test_speech_failure_recovers_gracefully(self):
  def broken(text): raise RuntimeError("SAPI unavailable")
  engine=ui_voice.VoiceEngine(config(),speaker=broken)
  try:
   for _ in range(4): self.assertIsInstance(engine.say("MANTIS."),bool)
   engine.drain(3.); time.sleep(.3)
   self.assertFalse(engine.status.available)
   self.assertFalse(engine.say("MANTIS."))
  finally: engine.stop()

 def test_speech_text_is_sanitised_before_powershell(self):
  self.assertNotIn("'",ui_voice.sanitize("BTC'; Remove-Item C:\\ ;'"))
  self.assertNotIn(";",ui_voice.sanitize("a;b"))
  self.assertNotIn("$",ui_voice.sanitize("$env:PATH"))

 def test_voice_never_blocks_the_caller(self):
  release=threading.Event()
  def slow(text): release.wait(2.0)
  engine=ui_voice.VoiceEngine(config(),speaker=slow)
  try:
   started=time.monotonic()
   for _ in range(6): engine.say("MANTIS.")
   self.assertLess(time.monotonic()-started,.5)
  finally: release.set(); engine.stop()

 def test_spoken_asset_names(self):
  self.assertEqual(ui_voice.spoken_asset("BTC-USD"),"Bitcoin")
  self.assertEqual(ui_voice.spoken_asset("ADA-USD"),"Cardano")
  self.assertEqual(ui_voice.spoken_asset("DOGE-USD"),"DOGE")


# ---------------------------------------------------------------------------
# State mapping
# ---------------------------------------------------------------------------

class StateMappingTests(unittest.TestCase):
 def test_view_copies_backend_fields_verbatim(self):
  view=view_from_snapshot(snapshot())
  for field in ("asset","contract_id","reference","current_price","buffer","p_yes","p_no",
                "predicted_side","conservative_bound","fragility","disagreement",
                "crossing_probability","reference_crossings","final_decision","final_reason"):
   self.assertEqual(getattr(view,field),getattr(snapshot(),field),field)
  self.assertEqual(view.classification_state,"ENTER YES")
  self.assertTrue(view.has_data)

 def test_metadata_is_surfaced_for_diagnostics(self):
  view=view_from_snapshot(snapshot(metadata={"git_commit":"abc123","config_hash":"deadbeef",
   "provider_mode":"verified-manual","volatility_regime":"HIGH","quality_reason":"OK"}))
  self.assertEqual(view.git_commit,"abc123")
  self.assertEqual(view.provider_mode,"verified-manual")
  self.assertEqual(view.volatility_regime,"HIGH")

 def test_countdown_uses_the_backend_resolution_timestamp(self):
  end=NOW+timedelta(seconds=277)
  view=view_from_snapshot(snapshot(window_end=end.isoformat(),seconds_remaining=999.))
  self.assertAlmostEqual(view.seconds_remaining(NOW),277.,places=6)
  self.assertEqual(theme.format_countdown(view.seconds_remaining(NOW)),"T-04:37")

 def test_countdown_never_goes_negative(self):
  view=view_from_snapshot(snapshot(window_end=(NOW-timedelta(minutes=1)).isoformat()))
  self.assertEqual(view.seconds_remaining(NOW),0.)
  self.assertEqual(theme.format_countdown(view.seconds_remaining(NOW)),"T-00:00")

 def test_buffer_z_matches_the_value_the_engine_gives_the_policy(self):
  """Pin the diagnostics normalisation against ForwardEngine so it cannot drift."""
  state=asset_state()
  engine_value=state.buffer/state.reference/state.volatility_estimate
  with tempfile.TemporaryDirectory() as tmp:
   snap=ForwardEngine(ForwardStore(Path(tmp)),run_id="run").process(state)
  self.assertAlmostEqual(view_from_snapshot(snap).buffer_z,engine_value,places=12)

 def test_spark_history_is_bounded_and_real(self):
  cfg=config(); state=CommandCenterState(ASSETS,cfg)
  for i in range(120): state.update_from_snapshot(snapshot(p_yes=i/200.))
  view=state.view("BTC-USD")
  self.assertLessEqual(len(view.spark),60)
  self.assertAlmostEqual(view.spark[-1],119/200.)

 def test_event_log_is_capped(self):
  state=CommandCenterState(ASSETS,config(event_log_length=10))
  for i in range(50): state.log("INFO","SYSTEM",f"E{i}")
  self.assertEqual(len(state.snapshot().events),10)

 def test_mark_no_data_never_shows_stale_numbers(self):
  state=CommandCenterState(ASSETS,config())
  state.update_from_snapshot(snapshot())
  state.mark_no_data("BTC-USD")
  view=state.view("BTC-USD")
  self.assertFalse(view.has_data)
  self.assertEqual(view.final_decision,"DATA HOLD")
  self.assertIsNone(view.current_price); self.assertIsNone(view.p_yes)

 def test_focus_prefers_an_entering_asset_without_ranking_them(self):
  state=CommandCenterState(ASSETS,config(default_focused_asset="BTC-USD"))
  self.assertEqual(state.snapshot().focus,"BTC-USD")
  state.update_from_snapshot(snapshot(asset="SOL-USD",final_decision="ENTER YES",
   timestamp_utc=NOW.isoformat()))
  state.update_from_snapshot(snapshot(asset="XRP-USD",final_decision="ENTER NO",
   timestamp_utc=(NOW+timedelta(seconds=10)).isoformat()))
  self.assertEqual(state.snapshot().focus,"SOL-USD")   # longest-standing ENTER

 def test_rollover_clears_the_previous_window(self):
  state=CommandCenterState(ASSETS,config())
  state.update_from_snapshot(snapshot())
  state.clear_window("C2")
  view=state.view("BTC-USD")
  self.assertEqual(view.contract_id,"C2")
  self.assertFalse(view.has_data)
  self.assertIsNone(view.p_yes)


# ---------------------------------------------------------------------------
# Decision presentation
# ---------------------------------------------------------------------------

class DecisionDisplayTests(unittest.TestCase):
 def test_every_backend_decision_has_a_style(self):
  for decision in Decision:
   self.assertIsNot(theme.decision_style(decision.value),theme.UNKNOWN,decision.value)

 def test_states_differ_by_more_than_colour(self):
  styles=[theme.decision_style(d.value) for d in Decision]
  for attribute in ("label","glyph","ascii_glyph","shape"):
   values=[getattr(s,attribute) for s in styles]
   self.assertEqual(len(set(values)),len(values),attribute)

 def test_enter_states_are_emphasised_and_visually_distinct(self):
  yes=theme.decision_style("ENTER YES"); no=theme.decision_style("ENTER NO")
  self.assertTrue(yes.emphasis and no.emphasis)
  self.assertNotEqual(yes.style,no.style)
  self.assertNotEqual(yes.shape,no.shape)

 def test_wait_is_neutral_and_no_trade_is_not_alarming(self):
  self.assertFalse(theme.decision_style("WAIT").emphasis)
  self.assertFalse(theme.decision_style("NO TRADE THIS CONTRACT").emphasis)
  self.assertTrue(theme.decision_style("DATA HOLD").emphasis)

 def test_reason_is_rendered_beneath_the_decision(self):
  frame=render_frame(final_decision="WAIT",final_reason="PROBABILITY_TOO_LOW")
  self.assertIn("WAIT",frame)
  self.assertIn("CONFIDENCE BELOW THRESHOLD",frame)

 def test_every_backend_reason_code_has_operator_text(self):
  from mantis_v4.entry import decision as entry_decision
  from mantis_v4.economics import decision as econ_decision
  codes=set()
  for module in (entry_decision,econ_decision):
   source=inspect.getsource(module)
   for line in source.splitlines():
    if "reason_code=" in line or "DecisionResult(Decision." in line:
     for token in line.replace('"',' ').replace("'",' ').split():
      if token.isupper() and "_" in token and token not in ("ENTER_YES","ENTER_NO","NO_TRADE","DATA_HOLD"):
       codes.add(token)
  missing=sorted(c for c in codes if c not in theme.REASON_TEXT)
  self.assertEqual(missing,[],f"unmapped reason codes: {missing}")


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_frame(width=200,height=50,cfg=None,ascii_only=False,**snapshot_kw):
 cfg=cfg or config()
 state=CommandCenterState(ASSETS,cfg)
 state.update_from_snapshot(snapshot(**snapshot_kw))
 return render_to_text(state,cfg,width=width,height=height,now=NOW,
                       ascii_only=ascii_only,color=False)


class RenderingTests(unittest.TestCase):
 def test_full_screen_frame_contains_every_required_region(self):
  frame=render_frame()
  for label in ("MANTIS","PRIMARY DECISION","ASSET SURVEILLANCE","CONTRACT ECONOMICS",
                "PROVIDER STATUS","EVENT LOG","OBSERVATION ONLY"):
   self.assertIn(label,frame,label)

 def test_rendering_with_missing_economics_still_shows_classification(self):
  frame=render_frame(ev_status="ECONOMICS_UNAVAILABLE",final_decision="WAIT",
   final_reason="CONTRACT_QUOTE_UNAVAILABLE",yes_ask=None,no_ask=None,break_even=None)
  self.assertIn("96.4%",frame)                       # classification survives
  self.assertIn("UNAVAILABLE",frame)
  self.assertIn("CONTRACT ECONOMICS UNAVAILABLE",frame)
  self.assertIn("EV ENGINE",frame)
  self.assertNotIn("0.000 / 0.000",frame)            # never a fabricated book

 def test_rendering_with_stale_data_holds_and_hides_numbers(self):
  cfg=config(); state=CommandCenterState(ASSETS,cfg)
  state.update_from_snapshot(snapshot())
  state.mark_no_data("BTC-USD","STALE_DATA")
  frame=render_to_text(state,cfg,width=200,height=50,now=NOW,color=False)
  self.assertIn("DATA HOLD",frame)
  self.assertIn("STALE UNDERLYING DATA",frame)
  self.assertNotIn("102.0000",frame)                 # the stale price is gone

 def test_no_current_data_card_is_explicit(self):
  cfg=config(); state=CommandCenterState(ASSETS,cfg)
  frame=render_to_text(state,cfg,width=200,height=50,now=NOW,color=False)
  self.assertIn("NO CURRENT DATA",frame)

 def test_countdown_block_renders_the_backend_duration(self):
  block=panels.big_text(theme.format_countdown(277))
  self.assertEqual(len(block),5)
  self.assertTrue(any("█" in row for row in block))

 def test_forward_and_historical_samples_are_labelled_separately(self):
  cfg=config(); state=CommandCenterState(ASSETS,cfg)
  state.update_from_snapshot(snapshot())
  state.set_forward({"sample_label":"SMALL SAMPLE","classification_accuracy":.94,
   "total_contracts_observed":10,"total_entry_events":6,"resolved_entries":5,
   "abstention_rate":.4},{"historical_accuracy":.9658,"historical_coverage":.6373,
   "status":"FORWARD_SAMPLE_TOO_SMALL"})
  frame=render_to_text(state,cfg,width=200,height=50,now=NOW,color=False)
  self.assertIn("FORWARD OBSERVATION SAMPLE",frame)
  self.assertIn("HISTORICAL PROXY HOLDOUT",frame)
  self.assertIn("SAMPLES ARE NEVER COMBINED",frame)
  self.assertIn("96.58%",frame); self.assertIn("94.00%",frame)

 def test_diagnostics_panel_is_hidden_by_default(self):
  self.assertNotIn("ADVANCED DIAGNOSTICS",render_frame())
  self.assertIn("ADVANCED DIAGNOSTICS",
                render_frame(cfg=config(show_advanced_diagnostics=True)))

 def test_demo_frames_are_prominently_labelled(self):
  cfg=config(demo_mode=True); state=CommandCenterState(ASSETS,cfg)
  state.set_status(demo_mode=True)
  for snap in DemoFeed(ASSETS).snapshots(NOW): state.update_from_snapshot(snap,synthetic=True)
  frame=render_to_text(state,cfg,width=200,height=50,now=NOW,color=False)
  self.assertIn("DEMO / SYNTHETIC DATA",frame)
  self.assertIn("SYNTHETIC",frame)

 def test_ascii_fallback_avoids_block_glyphs(self):
  frame=render_frame(ascii_only=True)
  for glyph in ("█","░","▲","▼","◆","⊘"):
   self.assertNotIn(glyph,frame,glyph)

 def test_error_panel_replaces_the_auxiliary_view(self):
  from mantis_v4.ui import errors as ui_errors
  cfg=config(); state=CommandCenterState(ASSETS,cfg)
  state.update_from_snapshot(snapshot())
  try: raise ValueError("provider exploded")
  except ValueError as exc:
   state.set_error(ui_errors.describe(exc,provider="yahoo",component="market data fetch"))
  frame=render_to_text(state,cfg,width=200,height=50,now=NOW,color=False)
  self.assertIn("SYSTEM ERROR",frame)
  self.assertIn("yahoo",frame); self.assertIn("market data fetch",frame)
  self.assertIn("retrying",frame)
  self.assertNotIn("Traceback",frame)               # tracebacks stay in the log file

 def test_developer_mode_exposes_the_traceback(self):
  from mantis_v4.ui import errors as ui_errors
  cfg=config(developer_mode=True); state=CommandCenterState(ASSETS,cfg)
  try: raise ValueError("boom")
  except ValueError as exc: state.set_error(ui_errors.describe(exc,component="scan"))
  frame=render_to_text(state,cfg,width=200,height=50,now=NOW,color=False)
  self.assertIn("Traceback",frame)

 def test_diagnostic_log_captures_the_full_traceback(self):
  from mantis_v4.ui import errors as ui_errors
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/"nested"/"diag.log"
   try: raise ValueError("boom")
   except ValueError as exc:
    ui_errors.write_diagnostic(ui_errors.describe(exc,component="scan"),path)
   self.assertIn("Traceback",path.read_text(encoding="utf-8"))


class ResizeTests(unittest.TestCase):
 def test_minimum_size_warning_instead_of_a_crash(self):
  cfg=config(); state=CommandCenterState(ASSETS,cfg)
  frame=render_to_text(state,cfg,width=60,height=18,now=NOW,color=False)
  self.assertIn("TERMINAL TOO SMALL",frame)
  self.assertIn("60 x 18",frame)
  self.assertIn("--no-ui",frame)

 def test_renders_across_a_range_of_terminal_sizes(self):
  cfg=config(); state=CommandCenterState(ASSETS,cfg)
  state.update_from_snapshot(snapshot())
  for width,height in ((96,30),(110,32),(120,40),(150,44),(200,50),(240,63),(400,90)):
   frame=render_to_text(state,cfg,width=width,height=height,now=NOW,color=False)
   self.assertIn("MANTIS",frame,f"{width}x{height}")
   longest=max(len(line) for line in frame.splitlines())
   self.assertLessEqual(longest,width,f"overflow at {width}x{height}")

 def test_all_assets_stay_visible_when_the_terminal_is_short(self):
  cfg=config(); state=CommandCenterState(ASSETS,cfg)
  for asset in ASSETS: state.update_from_snapshot(snapshot(asset=asset))
  frame=render_to_text(state,cfg,width=120,height=32,now=NOW,color=False)
  for asset in ASSETS:
   self.assertIn(asset.replace("-USD",""),frame,asset)

 def test_resize_between_frames_is_handled(self):
  cfg=config(); state=CommandCenterState(ASSETS,cfg)
  state.update_from_snapshot(snapshot())
  console=build_console(cfg,file=__import__("io").StringIO(),width=200,height=50,
                        force_terminal=True,legacy_windows=False)
  center=CommandCenter(state,cfg,console=console)
  self.assertTrue(center.fits())
  console.width=50; console.height=15
  self.assertFalse(center.fits())
  center.build(now=NOW)                     # must not raise at any size
  console.width=200; console.height=50
  self.assertTrue(center.fits())


# ---------------------------------------------------------------------------
# Isolation: presentation can never damage the engine
# ---------------------------------------------------------------------------

class BrokenState:
 """Every presentation call raises."""
 def log(self,*a,**k): raise RuntimeError("log failed")
 def snapshot(self): raise RuntimeError("snapshot failed")
 def update_from_snapshot(self,*a,**k): raise RuntimeError("update failed")


class IsolationTests(unittest.TestCase):
 def test_alert_handler_failure_does_not_escape_to_the_engine(self):
  cfg=config()
  router=AlertRouter(BrokenState(),FakeAudio(),FakeVoice(),cfg)
  bus=EventBus(); router.attach(bus)
  with tempfile.TemporaryDirectory() as tmp:
   store=ForwardStore(Path(tmp))
   engine=ForwardEngine(store,events=bus,run_id="run")
   snap=engine.process(asset_state())          # must complete normally
   self.assertEqual(snap.phase6_state,"ENTER YES")
   self.assertEqual(len(store.read("observations")),1)
   self.assertEqual(len(store.read("entries")),1)

 def test_render_failure_disables_the_interface_without_raising(self):
  cfg=config()
  center=CommandCenter(BrokenState(),cfg,
                       console=build_console(cfg,file=__import__("io").StringIO(),width=200,height=50))
  for _ in range(10): center.render_once()
  self.assertIsNotNone(center.disabled_reason)

 def test_scan_loop_survives_a_failing_asset(self):
  """The runner's per-asset guard keeps the loop alive."""
  source=(REPO/"mantis_v4_live.py").read_text(encoding="utf-8")
  self.assertIn("except Exception as exc:",source)
  self.assertIn("report_error(exc,component=f\"scan {asset}\"",source)

 def test_voice_and_audio_workers_are_daemon_threads(self):
  audio=ui_audio.AudioEngine(config(),backend=lambda f,d:None)
  voice=ui_voice.VoiceEngine(config(),speaker=lambda t:None)
  try:
   self.assertTrue(audio._thread.daemon)
   self.assertTrue(voice._thread.daemon)
  finally: audio.stop(); voice.stop()

 def test_queues_shed_load_instead_of_blocking(self):
  self.assertLessEqual(ui_audio.MAX_PENDING,16)
  self.assertLessEqual(ui_voice.MAX_PENDING,8)


# ---------------------------------------------------------------------------
# Safe modes never reach the forward logs
# ---------------------------------------------------------------------------

def code_identifiers(source):
 """Every name/attribute/import actually referenced by the code.

 Parsed rather than grepped so a docstring that *mentions* ``ForwardStore``
 cannot fail the check, and so a real reference cannot hide behind formatting.
 """
 import ast,textwrap
 tree=ast.parse(textwrap.dedent(source)); names=set()
 for node in ast.walk(tree):
  if isinstance(node,ast.Name): names.add(node.id)
  elif isinstance(node,ast.Attribute): names.add(node.attr)
  elif isinstance(node,ast.ImportFrom):
   names.update(a.name for a in node.names); names.add(node.module or "")
  elif isinstance(node,ast.Import): names.update(a.name for a in node.names)
 return names


class SafeModeTests(unittest.TestCase):
 def test_demo_module_cannot_reach_the_store(self):
  names=code_identifiers(inspect.getsource(__import__("mantis_v4.ui.demo",fromlist=["demo"])))
  for forbidden in ("ForwardStore","ForwardEngine","ForwardConfig","LiveAssetState","store"):
   self.assertNotIn(forbidden,names,forbidden)

 def test_demo_run_writes_nothing_to_the_forward_logs(self):
  with tempfile.TemporaryDirectory() as tmp:
   store=ForwardStore(Path(tmp))
   before={name:store.path(name).exists() for name in ForwardStore.FILES}
   cfg=config(demo_mode=True); state=CommandCenterState(ASSETS,cfg)
   router,_,audio,voice=router_with(); bus=EventBus(); router.attach(bus)
   feed=DemoFeed(ASSETS)
   for _ in range(5):
    snaps=feed.snapshots(NOW)
    for snap in snaps: state.update_from_snapshot(snap,synthetic=True)
    emit_demo_events(bus,snaps)
   render_to_text(state,cfg,width=200,height=50,now=NOW,color=False)
   for name in ForwardStore.FILES:
    self.assertEqual(store.path(name).exists(),before[name],name)
    self.assertEqual(store.read(name),[],name)

 def test_demo_snapshots_are_marked_synthetic(self):
  for snap in DemoFeed(ASSETS).snapshots(NOW):
   self.assertEqual(snap.run_id,"DEMO")
   self.assertTrue(snap.metadata.get("synthetic"))

 def test_alert_test_module_cannot_reach_the_store(self):
  names=code_identifiers(inspect.getsource(ui_alerts.run_test_alerts))
  for forbidden in ("ForwardStore","ForwardEngine","LiveAssetState","store","engine"):
   self.assertNotIn(forbidden,names,forbidden)

 def test_alert_test_writes_nothing_to_the_forward_logs(self):
  with tempfile.TemporaryDirectory() as tmp:
   store=ForwardStore(Path(tmp))
   audio=FakeAudio(); voice=FakeVoice()
   played=ui_alerts.run_test_alerts(audio,voice,config(),pause=0.,emit=lambda *a:None)
   self.assertEqual(played,["enter_yes","enter_no","data_hold","rollover","resolution","error"])
   for name in ForwardStore.FILES:
    self.assertEqual(store.read(name),[],name)

 def test_test_alerts_covers_every_cue(self):
  self.assertEqual({cue for _,cue,_ in ui_alerts.TEST_SEQUENCE}|{"provider_failure"},
                   set(ui_audio.CUES))

 def test_no_order_placement_anywhere_in_the_presentation_layer(self):
  forbidden=("place_order","submit_order","buy(","sell(","order_id","execute_trade")
  for path in sorted((REPO/"mantis_v4"/"ui").glob("*.py"))+[REPO/"mantis_v4_live.py"]:
   text=path.read_text(encoding="utf-8").lower()
   for token in forbidden:
    self.assertNotIn(token,text,f"{path.name}: {token}")


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

class SettingsTests(unittest.TestCase):
 def test_defaults_load_without_any_files(self):
  cfg=PresentationConfig.load(local_file=Path("does-not-exist.json"),environ={})
  self.assertTrue(cfg.ui_enabled)
  self.assertFalse(cfg.audio_wait)          # WAIT silent by default

 def test_file_then_environment_precedence(self):
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/"ui.json"
   path.write_text('{"ui_refresh_rate": 2, "voice_enabled": false}',encoding="utf-8")
   cfg=PresentationConfig.load(local_file=path,environ={})
   self.assertEqual(cfg.ui_refresh_rate,2.)
   self.assertFalse(cfg.voice_enabled)
   cfg=PresentationConfig.load(local_file=path,environ={"MANTIS_UI_VOICE_ENABLED":"1"})
   self.assertTrue(cfg.voice_enabled)

 def test_unknown_setting_is_rejected_not_ignored(self):
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/"ui.json"; path.write_text('{"colour_scheme": "neon"}',encoding="utf-8")
   with self.assertRaises(ValueError): PresentationConfig.load(local_file=path,environ={})

 def test_invalid_values_are_rejected(self):
  for kw in ({"ui_refresh_rate":0.},{"master_volume":2.},{"voice_volume":900},
             {"event_log_length":0},{"voice_rate":99}):
   with self.assertRaises(ValueError): config(**kw)

 def test_cli_flags_win(self):
  class Args: no_ui=True; no_audio=True; no_voice=True; diagnostics=True
  cfg=PresentationConfig().apply_cli(Args())
  self.assertFalse(cfg.ui_enabled or cfg.audio_enabled or cfg.voice_enabled)
  self.assertTrue(cfg.show_advanced_diagnostics)

 def test_runner_exposes_the_documented_flags(self):
  import importlib
  parser=importlib.import_module("mantis_v4_live").build_parser()
  options={o for action in parser._actions for o in action.option_strings}
  for flag in ("--once","--no-ui","--no-voice","--no-audio","--diagnostics",
               "--test-alerts","--demo","--no-startup"):
   self.assertIn(flag,options,flag)


class StartupTests(unittest.TestCase):
 def test_sequence_reports_real_subsystem_state(self):
  from mantis_v4.ui import startup as ui_startup
  checks=ui_startup.build_checks(model_version="NORMAL_Z_PHASE6_LOCKED",
   policy_name="H_p0.95_l0.90_f50_d.05_t300",provider_state="READY",
   webull_status="AUTH_NOT_CONFIGURED",economics_status="DISABLED",forward_dir="data/forward",
   clock_ok=True,audio_status="winsound",voice_status="READY")
  text="\n".join(ui_startup.plain_lines(checks))
  for label in ("QUANT ENGINE","MODEL","ENTRY POLICY","DATA PROVIDER","WEBULL",
                "ECONOMICS ENGINE","FORWARD LOGGER","CLOCK SYNC","SYSTEM READY"):
   self.assertIn(label,text,label)
  self.assertIn("H_p0.95_l0.90_f50_d.05_t300",text)
  self.assertIn("AUTH NOT CONFIGURED",text)

 def test_failed_clock_marks_the_system_degraded(self):
  from mantis_v4.ui import startup as ui_startup
  checks=ui_startup.build_checks(model_version="M",policy_name="P",provider_state="READY",
   webull_status="AUTH_NOT_CONFIGURED",economics_status="DISABLED",forward_dir="d",
   clock_ok=False,audio_status="winsound",voice_status="READY")
  self.assertIn("SYSTEM DEGRADED","\n".join(ui_startup.plain_lines(checks)))


# ---------------------------------------------------------------------------
# Nothing below Phase 9 moved
# ---------------------------------------------------------------------------

class UnchangedSubsystemTests(unittest.TestCase):
 def test_phase6_policy_unchanged(self):
  p=Phase7Config().policy().classification
  self.assertEqual((p.name,p.probability_threshold,p.lcb_threshold,p.max_fragility,
                    p.max_disagreement,p.max_seconds_remaining,p.max_crossing_probability,
                    p.max_crossings),
                   ("H_p0.95_l0.90_f50_d.05_t300",.95,.90,50,.05,300,.35,4))

 def test_phase7_formulas_unchanged(self):
  self.assertAlmostEqual(break_even_probability(.6,1,.02),.62)
  self.assertAlmostEqual(expected_value(.95,.6,1,.03),.32)
  self.assertAlmostEqual(expected_value(.95,.6,1.),.35)
  self.assertAlmostEqual(expected_value(.90,.6,1.),.30)

 def test_phase8_forward_config_semantics_unchanged(self):
  cfg=ForwardConfig()
  self.assertEqual(cfg.software_version,"MANTIS_V4_PHASE8")
  self.assertEqual(cfg.model_version,"NORMAL_Z_PHASE6_LOCKED")
  self.assertTrue(cfg.observation_only)
  self.assertEqual(set(ForwardStore.FILES),
                   {"observations","entries","resolutions","provider_health","runs",
                    "window_events","session_events","primary_selections"})

 def test_phase8_observation_schema_unchanged(self):
  with tempfile.TemporaryDirectory() as tmp:
   store=ForwardStore(Path(tmp))
   ForwardEngine(store,events=EventBus(),run_id="run").process(asset_state())
   row=store.read("observations")[0]
  expected={"run_id","observation_id","timestamp_utc","timestamp_local","asset","contract_id",
   "window_start","window_end","seconds_remaining","reference","reference_status",
   "current_price","buffer","volatility_estimate","p_yes","p_no","predicted_side",
   "conservative_bound","fragility","disagreement","crossing_probability",
   "reference_crossings","phase6_state","phase6_reason","final_decision","final_reason",
   "data_age_seconds","fetch_latency_seconds","next_scan_utc","yes_bid","yes_ask","no_bid",
   "no_ask","quote_timestamp","quote_age","quote_status","break_even","model_edge","lcb_edge",
   "point_ev","lcb_ev","expected_return_on_cost","fees_status","slippage_status","ev_status",
   "outcome_status","user_action","metadata"}
  self.assertEqual(set(row),expected)

 def test_presentation_attached_engine_still_logs_identically(self):
  """A UI-attached engine and a bare engine produce identical persisted rows."""
  drop=("run_id","observation_id","timestamp_utc","timestamp_local","next_scan_utc","metadata")
  rows=[]
  for events in (None,EventBus()):
   with tempfile.TemporaryDirectory() as tmp:
    store=ForwardStore(Path(tmp))
    if events is not None:
     AlertRouter(CommandCenterState(ASSETS,config()),FakeAudio(),FakeVoice(),
                 config()).attach(events)
    ForwardEngine(store,events=events,run_id="run").process(asset_state())
    rows.append({k:v for k,v in store.read("observations")[0].items() if k not in drop})
  self.assertEqual(rows[0],rows[1])

 def test_v3_unchanged(self):
  path=REPO/"mantis_15m_resolution_v3.py"
  self.assertEqual(hashlib.sha1(path.read_bytes()).hexdigest(),
                   "cb7c65e3b00be5446c3bc403d7c043e39dc95eda")

 def test_ui_package_imports_no_decision_mathematics(self):
  """The UI may read backend results; it may not import the deciders."""
  banned=("from ..entry","from ..simulation","from ..models","import numpy","from ..backtest")
  for path in sorted((REPO/"mantis_v4"/"ui").glob("*.py")):
   text=path.read_text(encoding="utf-8")
   for token in banned:
    self.assertNotIn(token,text,f"{path.name}: {token}")


if __name__=="__main__": unittest.main()
