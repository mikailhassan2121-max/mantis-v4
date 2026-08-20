#!/usr/bin/env python3
"""MANTIS live observation runner and Phase 9 command center. No order placement.

Layering, top to bottom:

    quant       compute_asset_state()  -- unchanged Phase 5/6 mathematics
    providers   market data + economics chain
    state       ForwardEngine -> append-only ForwardStore (unchanged Phase 8)
    hooks       EventBus (ON_ENTRY_YES ... ON_ERROR)
    present     mantis_v4.ui: screen, event log, tones, speech

The scanner owns the main thread. Presentation runs on its own daemon threads
and is wrapped at every boundary, so a render, tone or speech failure degrades
the interface and never the scan loop.
"""
from __future__ import annotations
import argparse,json,math,time,uuid
from datetime import datetime,timezone
from pathlib import Path
import numpy as np
from mantis_v4.selection import SELECTION_POLICY_ID,select_primary
from mantis_v4.clock import Clock,Instant,load_timezone
from mantis_v4.config import MantisConfig
from mantis_v4.contracts import ContractWindow
from mantis_v4.backtest.features import precompute_indicators
from mantis_v4.providers.market_data import YFinanceMarketDataProvider
from mantis_v4.simulation import (combine_channels,compute_fragility,conservative_lower_bound,
 digital_sensitivities,gaussian_terminal,scaled_sensitivities,student_t_terminal)
from mantis_v4.economics import (EconomicsProviderChain,ManualEconomicsProvider,
 ProxyEconomicsProvider,WebullEconomicsProvider)
from mantis_v4.forward import (ForwardConfig,ForwardEngine,ForwardStore,LiveAssetState,
 ReferenceStatus,audit_contract,daily_report,forward_report,incorrect_signals,manifest,
 render_manifest,render_report)
from mantis_v4.forward.console import render_snapshot
from mantis_v4.forward.events import AppEvent,EventBus,EventType
UTC=timezone.utc
PHASE6_FRAGILITY_SCALES={"gamma":7.537633538712959,"vega":0.013047089075600496,"theta":0.004530053560656391,"vol_of_vol":0.3016632827895451}

def _scoped_path(root,value,label):
 target=(Path(root)/value).resolve()
 try: target.relative_to(Path(root).resolve())
 except ValueError as exc: raise ValueError(f"{label} must stay inside the project directory") from exc
 return target

# ---------------------------------------------------------------------------
# Quantitative core. Unchanged from Phase 8; extracted only so the runner's
# presentation wiring cannot be confused with its mathematics.
# ---------------------------------------------------------------------------

def compute_asset_state(*,asset,bars,latency,instant,window,cfg,crossings,last_side,economics,market):
 """Return a LiveAssetState, or None when the asset has no causal data yet."""
 frame=bars.frame; visible=frame[frame.index<instant.utc]
 opening=visible[visible.index>=window.start_utc]
 if opening.empty: return None,None
 reference=float(opening.iloc[0]["Open"]); spot=float(visible.iloc[-1]["Close"]); key=(window.contract_id,asset); side=spot>=reference
 if key in last_side and side!=last_side[key]: crossings[key]=crossings.get(key,0)+1
 last_side[key]=side; ind=precompute_indicators(visible); sigma1=float(ind.realized_vol_1m.iloc[-1]); seconds=window.seconds_remaining(instant); sigma_rem=sigma1*math.sqrt(max(seconds/60,1e-12)); buffer_pct=(spot-reference)/reference
 anchor=gaussian_terminal(np.array([buffer_pct]),np.array([sigma_rem]),spot=np.array([spot]),reference=np.array([reference])); heavy=student_t_terminal(np.array([buffer_pct]),np.array([sigma_rem]),nu=4.456245976114701,spot=np.array([spot])); channels={"gaussian":anchor.p_yes,"student_t":heavy.p_yes}; agreement=combine_channels(channels); lower=conservative_lower_bound(channels)
 sens=digital_sensitivities(spot=np.array([spot]),reference=np.array([reference]),seconds_remaining=np.array([seconds]),sigma_1m=np.array([sigma1])); scaled=scaled_sensitivities(sens,np.array([spot]),np.array([sigma1])); returns=np.diff(np.log(visible["Close"].astype(float).to_numpy())); rv5=float(np.std(returns[-5:],ddof=1)) if len(returns)>=5 else sigma1; rv30=float(np.std(returns[-30:],ddof=1)) if len(returns)>=30 else sigma1; vov=np.array([abs(math.log(rv5/rv30)) if rv5>0 and rv30>0 else 0.]); frag=compute_fragility(gamma_per_pct2=scaled["gamma_per_pct2"],vega_per_10pct_vol=scaled["vega_per_10pct_vol"],theta_per_30s=scaled["theta_per_30s"],abs_z=np.abs(sens.z),seconds_remaining=np.array([seconds]),crossings=np.array([crossings.get(key,0)]),vol_of_vol=vov,disagreement=agreement.disagreement,scales=PHASE6_FRAGILITY_SCALES)
 age=max(0.,(instant.utc-(bars.last_timestamp)).total_seconds()-60) if bars.last_timestamp else float("inf"); econ=economics.get_economics(asset,window.contract_id,instant.utc)
 state=LiveAssetState(asset,window.contract_id,window.start_utc,window.end_utc,instant.utc,reference,ReferenceStatus.PROXY,spot,sigma_rem,float(anchor.p_yes[0]),float(lower[0]),float(frag.score[0]),float(agreement.disagreement[0]),float(anchor.p_cross_reference[0]),crossings.get(key,0),age,latency,provider_mode=economics.last_provider or "proxy",economics=econ,quality_ok=np.isfinite(sigma1) and sigma1>0,quality_reason=market.health.display)
 return state,visible

# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

def build_parser():
 ap=argparse.ArgumentParser(description="MANTIS observation-only runner and command center")
 ap.add_argument("--once",action="store_true",help="perform one scan and exit")
 ap.add_argument("--forward-dir",default="data/forward")
 ap.add_argument("--manual-economics",default="config/contracts/live_economics.json")
 ui=ap.add_argument_group("presentation")
 ui.add_argument("--ui",choices=["web","terminal"],default=None,help="command center shell: 'web' (default, eDEX-style) or 'terminal' (Rich)")
 ui.add_argument("--no-browser",action="store_true",help="start the web shell but do not open a window (print the URL)")
 ui.add_argument("--port",type=int,default=None,metavar="N",help="port for the web shell (default: an OS-chosen free port)")
 ui.add_argument("--no-ui",action="store_true",help="plain console output instead of the command center")
 ui.add_argument("--no-audio",action="store_true",help="disable alert tones")
 ui.add_argument("--no-voice",action="store_true",help="disable spoken announcements")
 ui.add_argument("--no-startup",action="store_true",help="skip the initialization sequence")
 ui.add_argument("--diagnostics",action="store_true",help="show the advanced diagnostics panel")
 ui.add_argument("--operator-diagnostics",action="store_true",help="log operator-state/event propagation (development only)")
 ui.add_argument("--focus",default=None,metavar="ASSET",help="asset shown in the primary decision area by default")
 ui.add_argument("--profile",choices=["default","quiet","diagnostic"],default="default",help="small presentation/runtime profile")
 modes=ap.add_argument_group("safe modes (never write forward records)")
 modes.add_argument("--test-alerts",action="store_true",help="preview every alert tone and announcement, then exit")
 modes.add_argument("--demo",action="store_true",help="run the interface on synthetic states, clearly labelled")
 modes.add_argument("--health-check",action="store_true",help="inspect installation and configuration without starting the scanner")
 modes.add_argument("--self-test",action="store_true",help="run isolated persistence/web checks without touching forward data")
 modes.add_argument("--kalshi-check",action="store_true",help="check anonymous read-only Kalshi 15-minute event quotes and exit")
 modes.add_argument("--kalshi-forward-shadow",action="store_true",help="run isolated non-actionable Kalshi forward-shadow collection")
 modes.add_argument("--kalshi-manual-signals",action="store_true",help="run anonymous experimental manual-only Kalshi signals")
 modes.add_argument("--kalshi-manual-diagnostics",action="store_true",help="print one real Step 10 manual-signal scan and exit")
 modes.add_argument("--kalshi-forward-audit",action="store_true",help="audit isolated Kalshi forward-shadow records and exit")
 modes.add_argument("--kalshi-forward-report",action="store_true",help="report matured isolated Kalshi shadow observations and exit")
 modes.add_argument("--kalshi-shadow-dir",default="data/kalshi_forward_shadow",metavar="PATH",help="isolated Kalshi shadow data directory")
 modes.add_argument("--kalshi-manual-signal-dir",default="data/kalshi_manual_signal",metavar="PATH",help="isolated append-only experimental signal directory")
 modes.add_argument("--kalshi-shadow-simulate",type=int,metavar="WINDOWS",help="run deterministic accelerated Kalshi shadow simulation")
 modes.add_argument("--forward-report",action="store_true",help="print the read-only Phase 11 forward report and exit")
 modes.add_argument("--daily-report",nargs="?",const="TODAY",metavar="YYYY-MM-DD",help="print a UTC daily forward summary and exit")
 modes.add_argument("--forward-manifest",action="store_true",help="print forward dataset counts, schemas and hashes")
 modes.add_argument("--svi-report",action="store_true",help="print the read-only SVI operational evidence report")
 modes.add_argument("--svi-audit",action="store_true",help="audit SVI evidence identity and integrity")
 modes.add_argument("--svi-manifest",action="store_true",help="print SVI evidence counts, schemas and hashes")
 modes.add_argument("--svi-backtest",action="store_true",help="print deterministic read-only SVI historical scoring")
 modes.add_argument("--audit-contract",metavar="CONTRACT_ID",help="print one contract's immutable timeline")
 modes.add_argument("--incorrect-report",action="store_true",help="print incorrect entry-time classifications and exit")
 return ap

# ---------------------------------------------------------------------------
# Web shell. Read-only: the browser receives snapshots and can send nothing.
# ---------------------------------------------------------------------------

def _start_web(state,ui_config,root,on_ready=None):
 """Serve the command center and open it. Never fatal; returns the server."""
 from mantis_v4.ui.webserver import CommandCenterServer
 from mantis_v4.ui import webshell
 try:
  server=CommandCenterServer(state,ui_config).start()
 except Exception as exc:
  # Presentation is deliberately subordinate to scanning and persistence.
  # A missing port, broken renderer, or failed browser must never stop the
  # observation loop that owns the validated Phase 6-8 behavior.
  print(f"WEB INTERFACE DISABLED: {type(exc).__name__}: {exc}")
  return None
 print(f"MANTIS COMMAND CENTER  {server.url}")
 server.on_presentation_ready=on_ready
 state.set_status(http_server_status="LIVE")
 if ui_config.web_open_browser:
  profile=root/"data"/"ui-profile"
  launch_url=f"{server.url}?build={server.frontend_build_id}"
  process=webshell.launch(launch_url,profile_dir=profile,fullscreen=ui_config.web_fullscreen)
  server.browser_process=process
  state.set_status(browser_shell_status="LIVE" if process is not None else "UNAVAILABLE")
  if process is None:
   print("No Chromium-family browser found (Edge/Chrome/Brave).")
   print(f"Open this address manually: {server.url}")
 else:
  state.set_status(browser_shell_status="SUPPRESSED")
  print("Browser launch suppressed (--no-browser). Open the address above.")
 return server

# ---------------------------------------------------------------------------
# Safe modes
# ---------------------------------------------------------------------------

def run_test_alerts(ui_config):
 """Preview alerts. Constructs no store, no engine, and no asset state."""
 from mantis_v4.ui import build_audio,build_voice,run_test_alerts as preview
 audio=build_audio(ui_config); voice=build_voice(ui_config)
 try: preview(audio,voice,ui_config)
 finally: audio.stop(); voice.stop()
 return 0

def run_demo(ui_config,cfg,args):
 """Synthetic command center. Structurally cannot reach the forward logs.

 A full visual showcase: the feed walks every decision state, and the runner
 cycles the surrounding system states -- provider health, economics
 availability, contract rollover and resolution -- so each visual treatment can
 be inspected without waiting for the market to produce it.
 """
 from mantis_v4.ui import AlertRouter,CommandCenter,CommandCenterState,build_audio,build_voice
 from mantis_v4.ui.demo import (DemoFeed,DEMO_FORWARD_REPORT,DEMO_HISTORICAL,
  emit_demo_events,emit_demo_lifecycle,demo_provider_state)
 ui_config.demo_mode=True
 root=Path(__file__).resolve().parent
 assets=cfg.active_assets; state=CommandCenterState(assets,ui_config)
 state.set_status(demo_mode=True,run_id="DEMO",software_version="DEMO",model_version="DEMO_NORMAL_Z",
  policy_name="H_p0.95_l0.90_f50_d.05_t300",underlying_provider="synthetic",underlying_state="LIVE",
  webull_status="AUTH_NOT_CONFIGURED",economics_status="DISABLED",audio_enabled=ui_config.audio_enabled,
  voice_enabled=ui_config.voice_enabled,latency_seconds=0.0,started_at=datetime.now(UTC),
  git_commit="DEMO",config_hash="DEMO")
 state.set_forward(DEMO_FORWARD_REPORT,DEMO_HISTORICAL)
 state.log("INFO","SYSTEM","MANTIS ONLINE","demo / synthetic data")
 audio=build_audio(ui_config); voice=build_voice(ui_config); bus=EventBus()
 router=AlertRouter(state,audio,voice,ui_config).attach(bus)
 if args.operator_diagnostics:
  def trace_event(event):
   payload=event.payload or {}
   print("MANTIS_OPERATOR "+json.dumps({"timestamp":event.timestamp,"event_type":event.type.value,
    "asset":payload.get("asset"),"side":payload.get("side"),"contract_id":payload.get("contract_id"),
    "source":"EventBus","classification":"primary selection" if event.type is EventType.PRIMARY_SELECTION else "qualification" if event.type in (EventType.ENTRY_YES,EventType.ENTRY_NO) else "system"},default=str))
  for traced_type in EventType: bus.subscribe(traced_type,trace_event)

 web=ui_config.ui_mode=="web" and ui_config.ui_enabled and not args.once
 server=None; center=None
 if web:
  server=_start_web(state,ui_config,root,on_ready=router.arm)
 else:
  center=CommandCenter(state,ui_config,local_timezone=cfg.contract_timezone)
  if not args.once: center.start()

 feed=DemoFeed(assets); tick=0; last_primary=None
 try:
  if server is not None and ui_config.web_open_browser and getattr(server,"browser_process",None) is not None:
   # The browser owns presentation READY. Synthetic state remains still while
   # logos and the hidden hydration pass run.
   maximum=(ui_config.brand_prelude_duration_seconds+
    ui_config.technical_boot_duration_seconds+ui_config.startup_snapshot_timeout_seconds+
    ui_config.startup_settle_seconds+5.0)
   server.presentation_ready.wait(maximum)
  else:
   router.arm(announce=False)
  if not args.once and ui_config.demo_ready_delay_seconds:
   time.sleep(ui_config.demo_ready_delay_seconds)
  while True:
   snapshots=feed.snapshots()
   for snapshot in snapshots: state.update_from_snapshot(snapshot,synthetic=True)
   selection=select_primary(snapshots); state.set_primary_selection(selection)
   chosen=selection.get("selected"); primary_key=(chosen or {}).get("asset"),(chosen or {}).get("contract_id")
   if chosen and primary_key!=last_primary:
    bus.emit(AppEvent(EventType.PRIMARY_SELECTION,snapshots[0].timestamp_utc,chosen))
    last_primary=primary_key
   emit_demo_lifecycle(bus,snapshots,tick)
   provider,detail=demo_provider_state(tick)
   state.record_scan(underlying_state=provider,underlying_detail=detail,
    underlying_last_success=datetime.now(UTC),underlying_successes=tick*len(assets),
    latency_seconds=0.18+0.22*((tick%7)/7.0))
   tick+=1
   if args.once: break
   time.sleep(cfg.scan_interval_seconds)
 except KeyboardInterrupt: pass
 finally:
  from mantis_v4.runtime import shutdown_lines,stop_browser
  browser=getattr(server,"browser_process",None) if server is not None else None
  if center is not None:
   if args.once: center.render_once()
   center.stop()
  if server is not None: server.stop()
  audio.stop(); voice.stop()
  browser_stopped=stop_browser(browser) if browser is not None else None
  for line in shutdown_lines(forward_safe=True,server=None if server is None else True,
   reporter=None,audio=not getattr(audio,"is_alive",False),
   voice=not getattr(voice,"is_alive",False),browser=browser_stopped): print(line)
 return 0

# ---------------------------------------------------------------------------
# Live runner
# ---------------------------------------------------------------------------

def main():
 args=build_parser().parse_args(); root=Path(__file__).resolve().parent
 if args.kalshi_check:
  from mantis_v4.economics.kalshi import render_kalshi_check
  report,ready=render_kalshi_check(); print(report); return 0 if ready else 1
 if args.kalshi_forward_audit or args.kalshi_forward_report or args.kalshi_shadow_simulate:
  from mantis_v4.kalshi_forward import ShadowStore,audit_store,render_audit,shadow_report,simulate_shadow
  shadow_path=_scoped_path(root,args.kalshi_shadow_dir,"Kalshi shadow directory")
  if args.kalshi_shadow_simulate:
   result=simulate_shadow(shadow_path,args.kalshi_shadow_simulate)
   print(json.dumps(result,indent=2,sort_keys=True)); return 0 if result["audit"]["status"]=="PASS" else 1
  if args.kalshi_forward_report:
   print(json.dumps(shadow_report(ShadowStore(shadow_path)),indent=2,sort_keys=True)); return 0
  result=audit_store(ShadowStore(shadow_path)); print(render_audit(result)); return 0 if result["status"]=="PASS" else 1
 if args.svi_report or args.svi_audit or args.svi_manifest or args.svi_backtest:
  from saaf_ventures_intelligence.operations import audit_evidence,evidence_manifest,operational_report
  from saaf_ventures_intelligence.research import historical_replay_report
  shadow_path=_scoped_path(root,args.kalshi_shadow_dir,"Kalshi shadow directory")
  signal_path=_scoped_path(root,args.kalshi_manual_signal_dir,"Kalshi manual signal directory")
  evidence_path=signal_path/"svi"/"events.jsonl"; resolution_path=shadow_path/"resolutions.jsonl"
  if args.svi_report: value=operational_report(evidence_path,resolution_path)
  elif args.svi_backtest: value=historical_replay_report(evidence_path,resolution_path)
  elif args.svi_manifest: value=evidence_manifest(evidence_path,resolution_path)
  else: value=audit_evidence(evidence_path,resolution_path)
  print(json.dumps(value,indent=2,sort_keys=True)); return 0 if value.get("status",value.get("audit",{}).get("status","PASS"))=="PASS" else 1
 forward_path=_scoped_path(root,args.forward_dir,"forward directory")
 manual_path=_scoped_path(root,args.manual_economics,"manual economics path")
 args.forward_dir=str(forward_path.relative_to(root))
 args.manual_economics=str(manual_path.relative_to(root))
 from mantis_v4.ui import (AlertRouter,CommandCenter,CommandCenterState,PresentationConfig,
  Severity,build_audio,build_console,build_voice)
 from mantis_v4.ui import errors as ui_errors, startup as ui_startup
 ui_config=PresentationConfig.load().apply_cli(args)
 if args.forward_report or args.daily_report or args.forward_manifest or args.audit_contract or args.incorrect_report:
  report_store=ForwardStore(root/args.forward_dir)
  if args.forward_report: print(render_report(forward_report(report_store)))
  elif args.daily_report:
   day=None if args.daily_report=="TODAY" else args.daily_report
   print(render_report(daily_report(report_store,day),"MANTIS PHASE 11 DAILY FORWARD SUMMARY"))
  elif args.forward_manifest: print(render_manifest(manifest(report_store)))
  elif args.audit_contract: print(json.dumps(audit_contract(report_store,args.audit_contract),indent=2,sort_keys=True))
  else: print(json.dumps(incorrect_signals(report_store),indent=2,sort_keys=True))
  return 0
 if args.health_check:
  from mantis_v4.health import exit_code,inspect,render
  results=inspect(root,Path(args.forward_dir)); print(render(results)); return exit_code(results)
 if args.self_test:
  from mantis_v4.health import exit_code,render,self_test
  results=self_test(root); print(render(results,"MANTIS SELF-TEST")); return exit_code(results)
 if args.test_alerts: return run_test_alerts(ui_config)
 cfg=MantisConfig.load()
 # Operator live universe is versioned and intentionally narrower than the
 # historical research universe. Historical ADA records remain readable.
 cfg.assets=list(__import__("mantis_v4.selection",fromlist=["LIVE_ASSETS"]).LIVE_ASSETS)
 cfg.enabled_assets=list(cfg.assets)
 if args.kalshi_forward_shadow:
  return _run_kalshi_forward_shadow(root,args,cfg)
 if args.kalshi_manual_signals or args.kalshi_manual_diagnostics:
  return _run_kalshi_manual_signals(root,args,cfg,ui_config,diagnostic=args.kalshi_manual_diagnostics)
 if not cfg.voice_enabled: ui_config.voice_enabled=False
 if args.demo: return run_demo(ui_config,cfg,args)

 from mantis_v4.health import exit_code as health_exit,inspect as health_inspect
 preflight=health_inspect(root,Path(args.forward_dir))
 if health_exit(preflight):
  failed=[r for r in preflight if r.critical and not r.ok]
  raise RuntimeError("pre-flight failed: "+"; ".join(f"{r.component}: {r.detail}" for r in failed))
 print("MANTIS PREFLIGHT")
 print("QUANT CORE ............. READY")
 print("FORWARD STORE .......... READY")
 print("LOCAL SERVER ........... READY")
 auth=next((r.status for r in preflight if r.component=="WEBULL AUTH"),"NOT CONFIGURED")
 print(f"WEBULL ................. {auth}")

 clock=Clock(); tz=load_timezone(cfg.contract_timezone)
 store=ForwardStore(root/args.forward_dir); bus=EventBus(); qualification_bus=EventBus()
 for forwarded in (EventType.ROLLOVER,EventType.RESOLUTION):
  qualification_bus.subscribe(forwarded,bus.emit)
 market=YFinanceMarketDataProvider(interval=cfg.bar_interval,bootstrap_period=cfg.bootstrap_period,refresh_period=cfg.refresh_period,min_candles=cfg.min_candles,max_cache_rows=cfg.max_cache_rows,timeout_seconds=cfg.network_timeout_seconds,max_retries=cfg.max_retries,backoff_seconds=cfg.retry_backoff_seconds,cooldown_seconds=cfg.provider_cooldown_seconds)
 webull=WebullEconomicsProvider(cfg.credentials); manual=ManualEconomicsProvider(root/args.manual_economics)
 economics=EconomicsProviderChain([webull,manual,ProxyEconomicsProvider()])
 engine=ForwardEngine(store,ForwardConfig(cfg.scan_interval_seconds,cfg.max_data_age_seconds,cfg.contract_timezone),events=qualification_bus,repo=root,
  run_metadata={"asset_universe":list(cfg.active_assets),"config_profile":args.profile,
   "provider_mode":"yahoo","economics_provider_mode":"webull/manual/proxy",
   "webull_auth_status":webull.status,"selection_policy":SELECTION_POLICY_ID,"demo":False})
 # Rebuild crossing state from immutable observations after restart.
 crossings={}; last_side={}
 for o in store.read("observations"):
  key=(o.get("contract_id"),o.get("asset")); crossings[key]=max(crossings.get(key,0),int(o.get("reference_crossings",0))); last_side[key]=float(o.get("buffer",0))>=0

 # -- presentation -----------------------------------------------------------
 state=CommandCenterState(cfg.active_assets,ui_config)
 audio=build_audio(ui_config); voice=build_voice(ui_config)
 router=AlertRouter(state,audio,voice,ui_config).attach(bus)
 state.set_status(run_id=engine.run_id,software_version=engine.config.software_version,
  model_version=engine.config.model_version,policy_name=engine.policy.classification.name,
  git_commit=engine.git_commit,config_hash=engine.config_hash,started_at=datetime.now(UTC),
  local_timezone=cfg.contract_timezone,underlying_provider="yahoo",
  webull_status=webull.status,economics_provider="underlying-proxy",
  economics_status="ACTIVE" if manual.status=="AVAILABLE" else "DISABLED",
  audio_enabled=ui_config.audio_enabled,voice_enabled=ui_config.voice_enabled)

 console=build_console(ui_config); graphical=ui_config.ui_enabled and not args.no_ui
 # The web shell runs its own boot sequence in the browser, so the terminal
 # startup animation would only duplicate it.
 web=graphical and ui_config.ui_mode=="web" and not args.once
 if ui_config.startup_animation and not web:
  checks=ui_startup.build_checks(model_version=engine.config.model_version,
   policy_name=engine.policy.classification.name,provider_state="READY",
   webull_status=webull.status,economics_status="ACTIVE" if manual.status=="AVAILABLE" else "DISABLED",
   forward_dir=str(Path(args.forward_dir)),clock_ok=True,audio_status=audio.status.detail or audio.status.backend,
   voice_status=voice.status.detail,demo_mode=False)
  if graphical: ui_startup.run(console,checks,animate=True)
  else:
   for line in ui_startup.plain_lines(checks): print(line)

 center=None; server=None
 if web:
  server=_start_web(state,ui_config,root,on_ready=router.arm)
 elif graphical:
  center=CommandCenter(state,ui_config,console=console,local_timezone=cfg.contract_timezone)
  # A single scan prints one frame instead of taking over the screen.
  if not args.once: center.start()
 else:
  print("MODE: OBSERVATION_ONLY | NO AUTOMATED EXECUTION")
  print("WEBULL_STATUS = "+webull.status)

 if not web or server is None:
  # Terminal/plain modes complete startup synchronously. A failed browser
  # presentation must not leave alerts permanently disarmed.
  router.arm(announce=not args.once)

 reporter=_start_forward_reporter(store,state,ui_config)
 state.set_status(forward_logger_status="LIVE" if reporter is not None else "DISABLED")
 state.log(Severity.INFO.value,"SYSTEM","MANTIS ONLINE",f"run {engine.run_id[:8]}")

 def report_error(exc,*,component,provider="n/a",recovery="retrying / degraded mode"):
  """One place converts an exception into an ON_ERROR hook plus a log entry."""
  error=ui_errors.describe(exc,provider=provider,component=component,recovery=recovery)
  ui_errors.write_diagnostic(error,ui_config.diagnostic_log_path(),
   max_bytes=ui_config.diagnostic_log_max_bytes,backups=ui_config.diagnostic_log_backups)
  state.set_error(error)
  try: bus.emit(AppEvent(EventType.ERROR,error.timestamp.isoformat(),
   {"component":component,"provider":provider,"message":error.message,"recovery":recovery}))
  except Exception: pass

 persistence_safe=True
 selection_window=None
 try:
  while True:
   persistence_failed=False
   instant=clock.capture(); window=ContractWindow.for_instant(instant,tz,cfg.contract_window_minutes)
   if selection_window!=window.contract_id:
    state.set_primary_selection({"selection_policy":SELECTION_POLICY_ID,"status":"NO TRADE",
     "selected":None,"strongest_candidate":None,"candidates":[],"earliest_entry_in_seconds":max(0,window.seconds_remaining(instant)-300)})
    selection_window=window.contract_id
   cycle_snapshots=[]
   for asset in cfg.active_assets:
    try:
     started=time.monotonic(); bars=market.get_bars(asset,instant); latency=time.monotonic()-started
     if bars is None or bars.is_empty:
      _record_window_event(store,engine.run_id,asset,window,instant,"PROVIDER FAILURE","UNDERLYING_DATA_UNAVAILABLE")
      _hold(state,bus,asset,instant,"UNDERLYING_DATA_UNAVAILABLE"); continue
     state_obj,visible=compute_asset_state(asset=asset,bars=bars,latency=latency,instant=instant,
      window=window,cfg=cfg,crossings=crossings,last_side=last_side,economics=economics,market=market)
     if state_obj is None:
      _record_window_event(store,engine.run_id,asset,window,instant,"INSUFFICIENT DATA","AWAITING_FIRST_SCAN")
      _hold(state,bus,asset,instant,"AWAITING_FIRST_SCAN"); continue
     snap=engine.process(state_obj)
     cycle_snapshots.append(snap)
     _present(state,center,snap,latency)
     for pending in store.unresolved_contracts(asset):
      observations=[o for o in store.read("observations") if o.get("contract_id")==pending["contract_id"] and o.get("asset")==asset]
      if not observations: continue
      end=datetime.fromisoformat(observations[0]["window_end"])
      if end>instant.utc: continue
      terminal_rows=visible[visible.index>=end]
      if terminal_rows.empty: continue
      engine.resolve(asset=asset,contract_id=pending["contract_id"],resolution_timestamp=end,terminal_value=float(terminal_rows.iloc[0]["Open"]),reference=float(pending["reference"]),resolution_source="PROXY_UNDERLYING",verified=False)
    except Exception as exc:
     # One asset's failure never ends the scan or the run.
     report_error(exc,component=f"scan {asset}",provider="yahoo")
     _record_window_event(store,engine.run_id,asset,window,instant,"PROVIDER FAILURE",type(exc).__name__)
     _hold(state,bus,asset,instant,"UNDERLYING_DATA_UNAVAILABLE")
     if isinstance(exc,OSError):
      persistence_failed=True; persistence_safe=False; break
   selection=select_primary(cycle_snapshots) if cycle_snapshots else None
   existing=store.primary_selection(window.contract_id)
   if existing:
    selection=existing.get("selection",selection)
   if selection is not None:
    state.set_primary_selection(selection,persisted=bool(existing))
    if args.operator_diagnostics:
     status=state.snapshot().status
     print("MANTIS_OPERATOR "+json.dumps({"timestamp":instant.utc.isoformat(),"event_type":"OPERATOR_STATE",
      "source":"main.selector","operator_state":state.operator_state(),
      "voice_counters":{"received":status.voice_events_received,"suppressed":status.voice_events_suppressed,
       "actionable":status.actionable_voice_events,"primary_created":status.primary_selections_created}},default=str))
   chosen=selection.get("selected") if selection else None
   if chosen and not existing:
    import hashlib
    selection_id=hashlib.sha256(f"{window.contract_id}|{SELECTION_POLICY_ID}".encode()).hexdigest()
    record={"selection_id":selection_id,"run_id":engine.run_id,"contract_id":window.contract_id,
     "timestamp_utc":instant.utc.isoformat(),"selection_policy":SELECTION_POLICY_ID,
     "asset_universe":list(cfg.active_assets),"selection":selection}
    if store.append("primary_selections",record,"selection_id"):
     state.set_primary_selection(selection,persisted=True)
     state.set_status(primary_selections_created=state.snapshot().status.primary_selections_created+1)
     bus.emit(AppEvent(EventType.PRIMARY_SELECTION,instant.utc.isoformat(),chosen))
   if persistence_failed: break
   try:
    store.append("provider_health",{"health_id":str(uuid.uuid4()),"timestamp_utc":instant.utc.isoformat(),"provider":"yahoo","state":market.health.state.value,"detail":market.health.detail,"successful_fetches":market.health.total_requests-market.health.total_failures,"failed_fetches":market.health.total_failures},"health_id")
   except OSError as exc:
    persistence_safe=False
    report_error(exc,component="forward logger",provider="filesystem",
     recovery="DATA HOLD — persistence unavailable; scanner stopping safely")
    for asset in cfg.active_assets: _hold(state,bus,asset,instant,"PERSISTENCE_UNAVAILABLE")
    break
   _sync_health(state,router,market,instant)
   if args.once: break
   elapsed=clock.monotonic()-instant.monotonic; clock.sleep(max(0,cfg.scan_interval_seconds-elapsed))
 except KeyboardInterrupt:
  state.log(Severity.NOTICE.value,"SYSTEM","SHUTDOWN REQUESTED","forward records intact")
 finally:
  from mantis_v4.runtime import shutdown_lines,stop_browser
  stopped_at=datetime.now(UTC)
  try:
   store.append("session_events",{"session_event_id":f"{engine.run_id}:STOP","run_id":engine.run_id,
    "timestamp_utc":stopped_at.isoformat(),"event":"STOP","clean_shutdown":persistence_safe},"session_event_id")
  except OSError: persistence_safe=False
  reporter_stopped=reporter.stop() if reporter is not None else None
  browser=getattr(server,"browser_process",None) if server is not None else None
  if center is not None:
   if args.once: center.render_once()
   center.stop()
  if server is not None: server.stop()
  audio.stop(); voice.stop()
  browser_stopped=stop_browser(browser) if browser is not None else None
  if center is not None and center.disabled_reason:
   print(f"INTERFACE DISABLED: {center.disabled_reason}")
  for line in shutdown_lines(forward_safe=persistence_safe,
   server=None if server is None else True,reporter=reporter_stopped,
   audio=not getattr(audio,"is_alive",False),voice=not getattr(voice,"is_alive",False),
   browser=browser_stopped): print(line)
  try: _print_session_summary(store,engine.run_id)
  except Exception: pass
 return 0

def _run_kalshi_forward_shadow(root,args,cfg):
 """Enter the isolated Step 9 runner before any live selector/UI/voice exists."""
 from mantis_v4.kalshi_forward.live import run_live_shadow
 shadow_path=_scoped_path(root,args.kalshi_shadow_dir,"Kalshi shadow directory")
 result=run_live_shadow(root=root,output=shadow_path,config=cfg,once=args.once)
 return 0 if result["audit"]["status"]=="PASS" else 1

def _run_kalshi_manual_signals(root,args,cfg,ui_config,diagnostic=False):
 """Anonymous experimental signals; no EventBus, account, or order capability."""
 from mantis_v4.manual_signal import initial_manual_selection
 from mantis_v4.manual_signal.core import V22_POLICY
 from mantis_v4.manual_signal.live import ManualSignalEngine,render_manual_diagnostics
 from mantis_v4.ui import CommandCenter,CommandCenterState,build_audio,build_voice
 from mantis_v4.ui.voice import phrase_experimental_manual_signal
 from saaf_ventures_intelligence.agents import (KalshiMarketImpliedBenchmark,MantisAdapter,
  ReferenceDistanceShadow)
 from saaf_ventures_intelligence.contracts import AgentContext
 from saaf_ventures_intelligence.events import JsonlEventSink,NullEventSink
 from saaf_ventures_intelligence.operations import operational_report
 from saaf_ventures_intelligence.supervisors import MarketSupervisor
 from saaf_ventures_intelligence.ui import publish_to_mantis
 shadow_path=_scoped_path(root,args.kalshi_shadow_dir,"Kalshi shadow directory")
 signal_path=_scoped_path(root,args.kalshi_manual_signal_dir,"Kalshi manual signal directory")
 state=CommandCenterState(cfg.active_assets,ui_config)
 state.set_status(run_id="EXPERIMENTAL",software_version="MANTIS 4.x",
  model_version="KALSHI_REFERENCE_V1",policy_name=V22_POLICY.version,
  underlying_provider="YAHOO_PROXY",underlying_state="STARTING",webull_status="NOT USED",
  economics_provider="KALSHI_PUBLIC_REST",economics_status="EXPERIMENTAL",
  audio_enabled=ui_config.audio_enabled,voice_enabled=ui_config.voice_enabled,
  started_at=datetime.now(UTC),reference_status="KALSHI TARGET / YAHOO PROXY",
  quote_status="STARTING",forward_logger_status="SHADOW")
 state.set_primary_selection(initial_manual_selection(V22_POLICY))
 engine=ManualSignalEngine(root=root,config=cfg,shadow_dir=shadow_path,signal_dir=signal_path)
 try:
  svi_events=NullEventSink() if diagnostic else JsonlEventSink(signal_path/"svi"/"events.jsonl")
 except Exception as exc:
  svi_events=NullEventSink()
  state.log("WARNING","SVI","EVIDENCE UNAVAILABLE",f"{type(exc).__name__}: {exc}")
 svi=MarketSupervisor((MantisAdapter(),KalshiMarketImpliedBenchmark(),ReferenceDistanceShadow()),events=svi_events)
 svi_evidence_path=signal_path/"svi"/"events.jsonl"
 svi_resolution_path=shadow_path/"resolutions.jsonl"
 svi_evidence_report={"status":"REPORT_ONLY","resolution_requirement":"OFFICIAL_VERIFIED_ONLY",
  "resolved_forecasts":0,"unresolved_forecasts":0,"groups":[],"model_activation":False}
 svi_resolution_count=-1
 state.set_status(run_id=engine.run_id,git_commit=engine.git_commit)
 audio=build_audio(ui_config); voice=build_voice(ui_config)
 clock=Clock(); tz=load_timezone(cfg.contract_timezone); server=None; center=None
 ready=False; pending=None
 last_memory_status="NORMAL"
 if not diagnostic and ui_config.ui_enabled and ui_config.ui_mode=="web" and not args.once:
  def armed():
   nonlocal ready
   ready=True
  server=_start_web(state,ui_config,root,on_ready=armed)
 elif not diagnostic and ui_config.ui_enabled:
  center=CommandCenter(state,ui_config,local_timezone=cfg.contract_timezone)
  if not args.once:center.start()
  ready=True
 try:
  while True:
   instant=clock.capture(); window=ContractWindow.for_instant(instant,tz)
   snapshots,selection,persisted=engine.scan(instant,window,persist_signal=not diagnostic)
   if engine.memory_status!=last_memory_status:
    state.log("WARNING" if engine.memory_status!="NORMAL" else "NOTICE","MEMORY",
     "MEMORY PRESSURE" if engine.memory_status!="NORMAL" else "MEMORY NORMAL",
     engine.memory_status)
    last_memory_status=engine.memory_status
   for snapshot in snapshots: state.update_from_snapshot(snapshot)
   state.set_primary_selection(selection,persisted=persisted)
   try:
    svi_result=svi.evaluate(AgentContext(instant.utc,"KALSHI","CRYPTO_EVENT_15M",
     {"selection":selection,"contract_id":window.contract_id}))
    resolution_count=int((selection.get("operator_state") or {}).get("forward_shadow_summary",{}).get("resolutions") or 0)
    if not diagnostic and resolution_count!=svi_resolution_count:
     svi_operations=operational_report(svi_evidence_path,svi_resolution_path)
     svi_evidence_report=dict(svi_operations["evidence"])
     svi_evidence_report["audit_status"]=svi_operations["audit"]["status"]
     svi_evidence_report["manifest_version"]=svi_operations["manifest"]["manifest_version"]
     svi_resolution_count=resolution_count
    publish_to_mantis(state,svi_result,svi_evidence_report)
   except Exception as exc:
    state.log("WARNING","SVI","SUPERVISOR DEGRADED",f"{type(exc).__name__}: {exc}")
   if args.operator_diagnostics and not diagnostic:
    print("MANTIS_MANUAL_OPERATOR "+json.dumps(selection["operator_state"],default=str,sort_keys=True))
   state.record_scan(last_scan_utc=instant.utc,underlying_state="LIVE",
    quote_status="KALSHI PUBLIC",economics_status="EXPERIMENTAL")
   if persisted: pending=selection.get("selected")
   if ready and pending:
    voice.say(phrase_experimental_manual_signal(pending["asset"],pending["side"],pending.get("ask")))
    pending=None
   if diagnostic:
    print(render_manual_diagnostics(selection))
   elif center is None and server is None:
    print(json.dumps({"mode":"EXPERIMENTAL_MANUAL_SIGNAL_ONLY","operator_state":selection["operator_state"],
     "authentication_count":0,"orders_submitted":0},indent=2,default=str))
   if args.once or diagnostic:break
   time.sleep(max(0.2,cfg.scan_interval_seconds))
 except KeyboardInterrupt:pass
 finally:
  from mantis_v4.runtime import stop_browser
  engine.close()
  browser=getattr(server,"browser_process",None) if server else None
  if center is not None:
   if args.once:center.render_once()
   center.stop()
  if server is not None:server.stop()
  audio.stop();voice.stop()
  if browser is not None:stop_browser(browser)
  print("KALSHI EXPERIMENTAL MANUAL SIGNAL STOPPED")
  print("AUTHENTICATION .......... NONE")
  print("ORDER CAPABILITY ........ DISABLED")
 return 0

# ---------------------------------------------------------------------------
# Presentation helpers. Each one is failure-isolated from the scan loop.
# ---------------------------------------------------------------------------

def _present(state,center,snap,latency):
 try:
  state.update_from_snapshot(snap)
  state.record_scan(latency_seconds=latency,last_scan_utc=datetime.now(UTC),
   reference_status=snap.reference_status,quote_status=snap.quote_status,
   economics_status="ACTIVE" if snap.ev_status not in ("ECONOMICS_UNAVAILABLE","UNAVAILABLE") else "DISABLED")
 except Exception:
  pass
 if center is None:
  try: print(render_snapshot(snap)); print("-"*78)
  except Exception: pass

def _hold(state,bus,asset,instant,reason):
 """Show a hold for an asset the engine could not evaluate. Writes nothing."""
 try: state.mark_no_data(asset,reason)
 except Exception: pass
 try: bus.emit(AppEvent(EventType.DATA_HOLD,instant.utc.isoformat(),{"asset":asset,"reason":reason}))
 except Exception: pass

def _record_window_event(store,run_id,asset,window,instant,status,reason):
 """Append one immutable category per asset/window/status; never an observation."""
 import hashlib
 token=f"{contract_key(window.contract_id,asset)}|{status}"
 event_id=hashlib.sha256(token.encode()).hexdigest()
 store.append("window_events",{"window_event_id":event_id,"run_id":run_id,"asset":asset,
  "contract_id":window.contract_id,"window_start":window.start_utc.isoformat(),
  "window_end":window.end_utc.isoformat(),"timestamp_utc":instant.utc.isoformat(),
  "status":status,"reason":reason},"window_event_id")

def contract_key(contract_id,asset): return f"{contract_id}|{asset}"

def _print_session_summary(store,run_id):
 observations=[o for o in store.read("observations") if o.get("run_id")==run_id]
 entries=[e for e in store.read("entries") if e.get("run_id")==run_id]
 resolutions=[r for r in store.read("resolutions") if r.get("run_id")==run_id]
 correct=sum(r.get("classification_correct") is True for r in resolutions)
 incorrect=sum(r.get("classification_correct") is False for r in resolutions)
 holds=sum(e.get("run_id")==run_id and e.get("status")=="DATA HOLD" for e in store.read("window_events"))
 provider=sum(e.get("run_id")==run_id and e.get("status")=="PROVIDER FAILURE" for e in store.read("window_events"))
 print("MANTIS SESSION COMPLETE")
 print(f"WINDOWS OBSERVED ....... {len({(o.get('contract_id'),o.get('asset')) for o in observations})}")
 print(f"SIGNALS ISSUED ......... {len(entries)}")
 print(f"RESOLVED / UNRESOLVED .. {len(resolutions)} / {sum(1 for e in entries if not any((r.get('contract_id'),r.get('asset'))==(e.get('contract_id'),e.get('asset')) for r in resolutions))}")
 print(f"CORRECT / INCORRECT .... {correct} / {incorrect}")
 print(f"DATA HOLDS / PROVIDER .. {holds} / {provider}")
 print("FORWARD LOG STATUS ..... SAFE")

def _sync_health(state,router,market,instant):
 try:
  health=market.health
  state.set_status(underlying_state=health.state.value,underlying_detail=health.detail,
   underlying_last_success=health.last_success_utc,
   underlying_successes=health.total_requests-health.total_failures,
   underlying_failures=health.total_failures)
  if not health.state.is_usable and health.detail:
   router.provider_failure("yahoo",health.detail)
 except Exception:
  pass

def _start_forward_reporter(store,state,ui_config):
 """Recompute the forward-validation summary off the scan thread."""
 if not ui_config.show_forward_validation: return None
 from mantis_v4.runtime import start_worker
 from mantis_v4.forward import compare_historical,forward_report
 def worker(stop):
  while not stop.is_set():
   try:
    report=forward_report(store,n_boot=200)
    state.set_forward(report,compare_historical(report,store.read("observations")))
    state.set_status(forward_logger_status="LIVE")
   except Exception as exc:
    state.set_status(forward_logger_status="DEGRADED")
    state.log("WARNING","FORWARD","REPORTER DEGRADED",f"{type(exc).__name__}: {exc}")
   stop.wait(max(15.0,ui_config.forward_report_interval_seconds))
 return start_worker("MANTIS-Forward",worker,daemon=True)

def cli():
 try: return main()
 except (ValueError,RuntimeError,OSError) as exc:
  print("MANTIS STARTUP FAILED")
  print(f"{type(exc).__name__}: {exc}")
  print("Run: python mantis_v4_live.py --health-check")
  return 2

if __name__=="__main__": raise SystemExit(cli())
