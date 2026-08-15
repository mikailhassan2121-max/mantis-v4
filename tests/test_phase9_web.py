"""Phase 9 web-shell tests.

Two jobs, the same two the Rich front end had to satisfy: prove the shell is
handed backend state faithfully, and prove it cannot damage anything below it.
The web layer adds a socket, so it also has to prove the socket is read-only.
"""
import contextlib,io,json,tempfile,threading,time,unittest,urllib.error,urllib.request
from unittest.mock import patch
from datetime import datetime,timedelta,timezone
from pathlib import Path

from mantis_v4.forward import EventBus,ForwardEngine,ForwardStore,LiveSnapshot
from mantis_v4.ui import CommandCenterServer,CommandCenterState,PresentationConfig,snapshot_payload
from mantis_v4.ui import webmodel,webshell
from mantis_v4.ui.demo import DemoFeed,demo_provider_state,emit_demo_events,emit_demo_lifecycle
from mantis_v4.ui.state import SystemStatus

UTC=timezone.utc; NOW=datetime(2026,8,14,18,11,tzinfo=UTC)
ASSETS=["BTC-USD","ETH-USD","SOL-USD","XRP-USD","ADA-USD"]
REPO=Path(__file__).resolve().parents[1]
WEB=REPO/"mantis_v4"/"ui"/"web"


def config(**kw):
 cfg=PresentationConfig()
 for key,value in kw.items(): setattr(cfg,key,value)
 return cfg.validate()

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

def populated(cfg=None):
 cfg=cfg or config()
 state=CommandCenterState(ASSETS,cfg)
 state.update_from_snapshot(snapshot())
 state.log("INFO","SYSTEM","MANTIS ONLINE","run test")
 return state,cfg


class PayloadTests(unittest.TestCase):
 def test_payload_is_json_serialisable(self):
  state,cfg=populated()
  text=json.dumps(snapshot_payload(state.snapshot(),cfg,NOW))
  self.assertGreater(len(text),500)

 def test_payload_carries_every_region_the_shell_draws(self):
  state,cfg=populated()
  payload=snapshot_payload(state.snapshot(),cfg,NOW)
  for key in ("status","assets","events","forward","historical","error","focus",
              "branding","presentation","reason_text"):
   self.assertIn(key,payload,key)

 def test_asset_fields_are_copied_verbatim_from_the_backend(self):
  state,cfg=populated()
  view=snapshot_payload(state.snapshot(),cfg,NOW)["assets"][0]
  source=snapshot()
  for field in ("p_yes","p_no","conservative_bound","fragility","disagreement",
                "crossing_probability","reference_crossings","reference","current_price","buffer"):
   self.assertEqual(view[field],getattr(source,field),field)

 def test_missing_values_are_null_never_zero(self):
  """A missing quote must stay missing: the shell renders null as an em-dash."""
  state,cfg=populated()
  view=snapshot_payload(state.snapshot(),cfg,NOW)["assets"][0]
  for field in ("yes_bid","yes_ask","no_bid","no_ask","break_even","model_edge",
                "lcb_edge","point_ev","lcb_ev","expected_return_on_cost"):
   self.assertIsNone(view[field],field)
  self.assertFalse(view["economics_available"])

 def test_no_data_asset_publishes_no_stale_numbers(self):
  state,cfg=populated()
  state.mark_no_data("BTC-USD","STALE_DATA")
  view=snapshot_payload(state.snapshot(),cfg,NOW)["assets"][0]
  self.assertFalse(view["has_data"])
  self.assertIsNone(view["current_price"]); self.assertIsNone(view["p_yes"])
  self.assertEqual(view["final"]["key"],"data_hold")

 def test_countdown_is_measured_against_the_backend_window_end(self):
  state,cfg=populated()
  view=snapshot_payload(state.snapshot(),cfg,NOW)["assets"][0]
  self.assertAlmostEqual(view["seconds_remaining"],300.,places=6)
  self.assertEqual(view["window_end"],(NOW+timedelta(minutes=5)).isoformat())

 def test_disabled_audio_and_voice_are_presentation_state_only(self):
  state,cfg=populated(config(audio_enabled=False,voice_enabled=False))
  payload=snapshot_payload(state.snapshot(),cfg,NOW)
  self.assertFalse(payload["presentation"]["audio_enabled"])
  self.assertFalse(payload["status"]["voice_enabled"])

 def test_degraded_provider_and_stale_age_are_copied_for_rendering(self):
  state,cfg=populated()
  state.set_status(underlying_state="DEGRADED",underlying_detail="cached bars")
  state.update_from_snapshot(snapshot(data_age_seconds=91.0))
  payload=snapshot_payload(state.snapshot(),cfg,NOW)
  self.assertEqual(payload["status"]["underlying_state"],"DEGRADED")
  self.assertEqual(payload["status"]["underlying_detail"],"cached bars")
  self.assertEqual(payload["assets"][0]["data_age_seconds"],91.0)

 def test_decision_descriptor_matches_the_shared_table(self):
  from mantis_v4.entry.decision import Decision
  from mantis_v4.ui import theme
  for decision in Decision:
   described=webmodel._decision(decision.value)
   self.assertEqual(described["label"],theme.decision_style(decision.value).label)
   self.assertNotEqual(described["key"],"unknown",decision.value)

 def test_traceback_is_withheld_unless_developer_mode(self):
  from mantis_v4.ui import errors as ui_errors
  try: raise ValueError("boom")
  except ValueError as exc: error=ui_errors.describe(exc,component="scan")
  self.assertEqual(webmodel.error_payload(error,False)["traceback"],"")
  self.assertIn("Traceback",webmodel.error_payload(error,True)["traceback"])

 def test_branding_puts_shg_over_the_system_and_mantis_over_the_subsystem(self):
  branding=webmodel.branding_payload()
  self.assertEqual(branding["system_owner_short"],"SHG")
  self.assertEqual(branding["product"],"MANTIS")
  self.assertIn("NEURO-TACTICAL",branding["expansion"].upper())


class ServerTests(unittest.TestCase):
 def setUp(self):
  self.state,self.cfg=populated()
  self.server=CommandCenterServer(self.state,self.cfg,host="127.0.0.1",port=0).start()
  self.addCleanup(self.server.stop)

 def get(self,path):
  with urllib.request.urlopen(self.server.url.rstrip("/")+path,timeout=5) as response:
   return response.status,response.read()

 def test_binds_loopback_only(self):
  self.assertEqual(self.server.host,"127.0.0.1")

 def test_serves_the_shell_and_its_assets(self):
  for path,needle in (("/",b"MANTIS"),("/index.html",b"ACTIVE CONTRACT"),
                      ("/app.css",b"--cut"),("/app.js",b"EventSource"),
                      ("/boot.js",b"SHG"),("/audio.js",b"enter_yes"),
                      ("/modules.js",b"renderContract"),("/format.js",b"countdown")):
   status,body=self.get(path)
   self.assertEqual(status,200,path)
   self.assertIn(needle,body,path)

 def test_serves_supplied_branding_without_exposing_other_files(self):
  for name in ("saaf_holdings_group.png","saaf_ventures.png","mantis_darpa.png"):
   status,body=self.get("/branding/"+name)
   self.assertEqual(status,200,name)
   self.assertTrue(body.startswith(b"\x89PNG"),name)
  with self.assertRaises(urllib.error.HTTPError) as caught:
   self.get("/branding/../../requirements.txt")
  self.assertEqual(caught.exception.code,404)

 def test_snapshot_endpoint_returns_live_state(self):
  status,body=self.get("/snapshot.json")
  payload=json.loads(body)
  self.assertEqual(status,200)
  self.assertEqual(payload["assets"][0]["asset"],"BTC-USD")

 def test_path_traversal_is_refused(self):
  for path in ("/../settings.py","/../../mantis_v4_live.py","/..%2fsettings.py"):
   with self.assertRaises(urllib.error.HTTPError) as caught:
    self.get(path)
   self.assertEqual(caught.exception.code,404,path)

 def test_unknown_path_is_not_found(self):
  with self.assertRaises(urllib.error.HTTPError) as caught: self.get("/nope")
  self.assertEqual(caught.exception.code,404)

 def test_the_socket_is_read_only(self):
  """No verb but GET is implemented, so the browser cannot mutate anything."""
  source=(REPO/"mantis_v4"/"ui"/"webserver.py").read_text(encoding="utf-8")
  for verb in ("do_POST","do_PUT","do_DELETE","do_PATCH"):
   self.assertNotIn(verb,source,verb)
  self.assertIn("def do_GET",source)

 def test_stream_delivers_a_frame_then_can_be_dropped(self):
  request=urllib.request.urlopen(self.server.url.rstrip("/")+"/stream",timeout=5)
  try:
   payload=b""
   deadline=time.monotonic()+5
   while b"\n\n" not in payload and time.monotonic()<deadline:
    payload+=request.read(1)
   self.assertTrue(payload.startswith(b"data: "))
   self.assertIn(b"\"assets\"",payload)
  finally:
   request.close()

 def test_stop_closes_threads_and_is_idempotent(self):
  push,serve=self.server._push_thread,self.server._serve_thread
  self.server.stop()
  self.assertFalse(push.is_alive()); self.assertFalse(serve.is_alive())
  self.server.stop()


class IsolationTests(unittest.TestCase):
 def test_a_failing_state_degrades_the_stream_and_never_raises(self):
  class Broken:
   def snapshot(self): raise RuntimeError("snapshot failed")
   def log(self,*a,**k): raise RuntimeError("log failed")
  server=CommandCenterServer(Broken(),config(ui_refresh_rate=30.))
  try:
   for _ in range(6):
    try: server.frame()
    except Exception: server.failures+=1
   self.assertGreaterEqual(server.failures,5)
  finally:
   server.stop()

 def test_server_threads_are_daemons(self):
  state,cfg=populated()
  server=CommandCenterServer(state,cfg,host="127.0.0.1",port=0).start()
  try:
   self.assertTrue(server._serve_thread.daemon)
   self.assertTrue(server._push_thread.daemon)
  finally:
   server.stop()

 def test_a_lagging_client_sheds_frames_instead_of_growing(self):
  from mantis_v4.ui.webserver import MAX_CLIENT_BACKLOG,_Client
  client=_Client()
  for i in range(MAX_CLIENT_BACKLOG*4): client.push(f"frame{i}")
  self.assertLessEqual(len(client.frames),MAX_CLIENT_BACKLOG)
  self.assertEqual(client.frames[-1],f"frame{MAX_CLIENT_BACKLOG*4-1}")

 def test_browser_launcher_reports_absence_instead_of_raising(self):
  self.assertIsNone(webshell.launch("http://127.0.0.1:1/",browser="definitely-not-a-browser"))

 def test_fullscreen_launcher_uses_kiosk_and_maximized_fallbacks(self):
  with patch("mantis_v4.ui.webshell.subprocess.Popen") as popen:
   webshell.launch("http://127.0.0.1:1/",browser="msedge.exe",fullscreen=True)
  arguments=popen.call_args.args[0]
  for flag in ("--start-fullscreen","--start-maximized","--kiosk",
               "--edge-kiosk-type=fullscreen"):
   self.assertIn(flag,arguments)

 def test_frontend_disconnect_cannot_stop_state_updates(self):
  state,cfg=populated()
  server=CommandCenterServer(state,cfg,host="127.0.0.1",port=0).start()
  try:
   request=urllib.request.urlopen(server.url.rstrip("/")+"/stream",timeout=5)
   request.read(1); request.close()
   state.update_from_snapshot(snapshot(current_price=107.0))
   self.assertEqual(state.snapshot().assets[0].current_price,107.0)
  finally:
   server.stop()

 def test_server_start_failure_is_nonfatal_to_runner(self):
  import mantis_v4_live
  state,cfg=populated()
  with patch("mantis_v4.ui.webserver.CommandCenterServer.start",side_effect=OSError("busy")):
   with contextlib.redirect_stdout(io.StringIO()) as output:
    server=mantis_v4_live._start_web(state,cfg,REPO)
  self.assertIsNone(server)
  self.assertIn("WEB INTERFACE DISABLED",output.getvalue())
  state.update_from_snapshot(snapshot(current_price=108.0))
  self.assertEqual(state.snapshot().assets[0].current_price,108.0)

 def test_web_layer_writes_nothing_to_the_forward_logs(self):
  with tempfile.TemporaryDirectory() as tmp:
   store=ForwardStore(Path(tmp))
   state,cfg=populated(config(demo_mode=True))
   server=CommandCenterServer(state,cfg,host="127.0.0.1",port=0).start()
   try:
    feed=DemoFeed(ASSETS); bus=EventBus()
    for tick in range(6):
     snaps=feed.snapshots(NOW)
     for snap in snaps: state.update_from_snapshot(snap,synthetic=True)
     emit_demo_events(bus,snaps); emit_demo_lifecycle(bus,snaps,tick)
     server.frame()
    for name in ForwardStore.FILES:
     self.assertEqual(store.read(name),[],name)
   finally:
    server.stop()


class DemoShowcaseTests(unittest.TestCase):
 def _collect(self):
  from mantis_v4.forward import EventType
  seen=[]
  bus=EventBus()
  for event_type in EventType: bus.subscribe(event_type,seen.append)
  snaps=DemoFeed(ASSETS).snapshots(NOW)
  for tick in range(1,15): emit_demo_lifecycle(bus,snaps,tick)
  return seen

 def test_lifecycle_emits_rollover_and_resolution_without_persistence(self):
  from mantis_v4.forward import EventType
  types={event.type for event in self._collect()}
  self.assertIn(EventType.ROLLOVER,types)
  self.assertIn(EventType.RESOLUTION,types)

 def test_lifecycle_payloads_are_marked_synthetic(self):
  seen=self._collect()
  self.assertTrue(seen)
  for event in seen: self.assertTrue(event.payload.get("synthetic"),event.payload)

 def test_provider_cycle_shows_degraded_and_recovers(self):
  states={demo_provider_state(t)[0] for t in range(20)}
  self.assertEqual(states,{"LIVE","DEGRADED"})

 def test_demo_walks_every_decision_state(self):
  from mantis_v4.ui.demo import SCRIPT
  self.assertEqual({row[2] for row in SCRIPT},
                   {"WAIT","ENTER YES","ENTER NO","NO TRADE THIS CONTRACT","DATA HOLD"})


class FrontendAssetTests(unittest.TestCase):
 """The shell is static files, so its guarantees are checked as text."""

 def test_every_asset_the_document_references_exists(self):
  html=(WEB/"index.html").read_text(encoding="utf-8")
  import re
  for match in re.findall(r'(?:src|href)="([^"]+)"',html):
   if match.startswith("/branding/"):
    root=REPO/("assets" if (REPO/"assets").is_dir() else "assests")/"branding"
    self.assertTrue((root/Path(match).name).is_file(),match)
   else:
    self.assertTrue((WEB/match).is_file(),match)

 def test_no_external_resource_is_referenced(self):
  """A command center must not depend on the network to draw itself."""
  for path in sorted(WEB.iterdir()):
   text=path.read_text(encoding="utf-8")
   for token in ("http://","https://","//cdn",".googleapis",".jsdelivr","unpkg"):
    self.assertNotIn(token,text,f"{path.name}: {token}")

 def test_content_security_policy_confines_the_shell_to_itself(self):
  html=(WEB/"index.html").read_text(encoding="utf-8")
  self.assertIn("Content-Security-Policy",html)
  for directive in ("default-src 'self'","script-src 'self'","object-src 'none'"):
   self.assertIn(directive,html,directive)

 def test_boot_identifies_shg_before_mantis(self):
  boot=(WEB/"boot.js").read_text(encoding="utf-8")
  holdings=boot.index('/branding/saaf_holdings_group.png')
  ventures=boot.index('/branding/saaf_ventures.png')
  mantis=boot.index('/branding/mantis_darpa.png')
  self.assertLess(holdings,ventures)
  self.assertLess(ventures,mantis)

 def test_boot_is_a_long_explicit_state_sequence(self):
  boot=(WEB/"boot.js").read_text(encoding="utf-8")
  order=["brandPrelude(screen, scale)","identityHandoff(screen, scale)",
         'setPhase(screen, "boot")',"runLog(log, scale)",
         'identity(screen, "SHG"','identity(screen, "MANTIS"',
         "assemble(scale, opts.onReady)"]
  positions=[boot.index(item) for item in order]
  self.assertEqual(positions,sorted(positions))
  self.assertGreaterEqual(PresentationConfig().boot_duration,12.0)

 def test_brand_images_receive_monochrome_scanned_treatment(self):
  css=(WEB/"app.css").read_text(encoding="utf-8")
  for token in ("grayscale(1)","invert(1)","brandScan","brandEcho","brand-reticle"):
   self.assertIn(token,css)

 def test_audio_is_synthesized_not_sampled(self):
  audio=(WEB/"audio.js").read_text(encoding="utf-8")
  self.assertIn("createOscillator",audio)
  for token in (".wav",".mp3",".ogg","howler","Howl("):
   self.assertNotIn(token,audio,token)

 def test_every_required_cue_exists(self):
  audio=(WEB/"audio.js").read_text(encoding="utf-8")
  for cue in ("shg_boot","init_pulse","panel_online","scan","enter_yes","enter_no",
              "data_hold","warning","rollover","resolution","critical","system_ready"):
   self.assertIn(cue+":",audio,cue)

 def test_wait_is_silent_in_the_web_shell_too(self):
  app=(WEB/"app.js").read_text(encoding="utf-8")
  self.assertIn("WAIT is deliberately silent",app)

 def test_no_order_placement_anywhere_in_the_web_shell(self):
  forbidden=("place_order","submit_order","execute_trade","order_id","/api/trade")
  for path in sorted(WEB.iterdir()):
   text=path.read_text(encoding="utf-8").lower()
   for token in forbidden:
    self.assertNotIn(token,text,f"{path.name}: {token}")

 def test_shell_never_posts_anything(self):
  for path in sorted(WEB.glob("*.js")):
   text=path.read_text(encoding="utf-8")
   self.assertNotIn('method: "POST"',text,path.name)
   self.assertNotIn("XMLHttpRequest",text,path.name)

 def test_frontend_contains_no_quant_formula_implementation(self):
  """The browser formats backend values; it must not recreate Phases 6/7."""
  text="\n".join(path.read_text(encoding="utf-8").lower()
                 for path in sorted(WEB.glob("*.js")))
  for token in ("normalcdf","norm.cdf","gaussian_terminal","break_even =",
                "model_edge =","point_ev =","lcb_ev =","classification_threshold"):
   self.assertNotIn(token,text,token)

 def test_countdown_uses_backend_clock_offset_and_window_end(self):
  app=(WEB/"app.js").read_text(encoding="utf-8")
  self.assertIn("server_time_utc",app)
  self.assertIn("window_end",app)
  self.assertNotIn("setMinutes(",app)

 def test_demo_warning_is_static_and_unambiguous(self):
  html=(WEB/"index.html").read_text(encoding="utf-8")
  self.assertIn("DEMO / SYNTHETIC DATA",html)
  self.assertIn("NOT A LIVE SIGNAL",html)


class SettingsTests(unittest.TestCase):
 def test_web_settings_have_defaults(self):
  cfg=PresentationConfig()
  self.assertEqual(cfg.ui_mode,"web")
  self.assertEqual(cfg.web_host,"127.0.0.1")
  self.assertTrue(cfg.boot_sequence_enabled)
  self.assertTrue(cfg.scanlines_enabled)
  self.assertGreaterEqual(cfg.boot_duration,12.0)
  self.assertGreater(cfg.startup_alert_suppression_seconds,cfg.boot_duration)

 def test_invalid_web_settings_are_rejected(self):
  for kw in ({"ui_mode":"neon"},{"web_port":99999},{"boot_duration":-1.},{"boot_duration":99.}):
   with self.assertRaises(ValueError,msg=str(kw)): config(**kw)

 def test_cli_selects_the_shell(self):
  class Args: ui="terminal"; no_browser=True; port=9001; no_startup=False
  cfg=PresentationConfig().apply_cli(Args())
  self.assertEqual(cfg.ui_mode,"terminal")
  self.assertFalse(cfg.web_open_browser)
  self.assertEqual(cfg.web_port,9001)

 def test_no_startup_also_skips_the_browser_boot(self):
  class Args: no_startup=True
  cfg=PresentationConfig().apply_cli(Args())
  self.assertFalse(cfg.boot_sequence_enabled)

 def test_runner_exposes_the_new_flags(self):
  import importlib
  parser=importlib.import_module("mantis_v4_live").build_parser()
  options={o for action in parser._actions for o in action.option_strings}
  for flag in ("--ui","--no-browser","--port"):
   self.assertIn(flag,options,flag)


class HostMetricTests(unittest.TestCase):
 def test_only_measurable_values_are_reported(self):
  from mantis_v4.ui import hostmetrics
  payload=hostmetrics.host_payload(clients=2)
  self.assertEqual(payload["clients"],2)
  self.assertGreater(payload["pid"],0)
  self.assertGreaterEqual(payload["threads"],1)
  # CPU/RSS are optional; when present they must be real numbers, not zeros
  # standing in for an unavailable measurement.
  for optional in ("cpu_percent","rss_mb"):
   if optional in payload:
    self.assertIsInstance(payload[optional],float)


if __name__=="__main__": unittest.main()
