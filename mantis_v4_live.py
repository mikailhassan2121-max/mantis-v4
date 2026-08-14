#!/usr/bin/env python3
"""MANTIS Phase 8 live observation runner. No order placement."""
from __future__ import annotations
import argparse,math,time,uuid
from datetime import datetime,timezone
from pathlib import Path
import numpy as np
from mantis_v4.clock import Clock,Instant,load_timezone
from mantis_v4.config import MantisConfig
from mantis_v4.contracts import ContractWindow
from mantis_v4.backtest.features import precompute_indicators
from mantis_v4.providers.market_data import YFinanceMarketDataProvider
from mantis_v4.simulation import (combine_channels,compute_fragility,conservative_lower_bound,
 digital_sensitivities,gaussian_terminal,scaled_sensitivities,student_t_terminal)
from mantis_v4.economics import (EconomicsProviderChain,ManualEconomicsProvider,
 ProxyEconomicsProvider,WebullEconomicsProvider)
from mantis_v4.forward import ForwardConfig,ForwardEngine,ForwardStore,LiveAssetState,ReferenceStatus
from mantis_v4.forward.console import render_snapshot
UTC=timezone.utc
PHASE6_FRAGILITY_SCALES={"gamma":7.537633538712959,"vega":0.013047089075600496,"theta":0.004530053560656391,"vol_of_vol":0.3016632827895451}

def main():
 ap=argparse.ArgumentParser(description="MANTIS Phase 8 observation-only runner")
 ap.add_argument("--once",action="store_true",help="perform one scan and exit")
 ap.add_argument("--forward-dir",default="data/forward"); ap.add_argument("--manual-economics",default="config/contracts/live_economics.json")
 args=ap.parse_args(); root=Path(__file__).resolve().parent; cfg=MantisConfig.load(); clock=Clock(); tz=load_timezone(cfg.contract_timezone)
 store=ForwardStore(root/args.forward_dir); engine=ForwardEngine(store,ForwardConfig(cfg.scan_interval_seconds,cfg.max_data_age_seconds,cfg.contract_timezone),repo=root)
 market=YFinanceMarketDataProvider(interval=cfg.bar_interval,bootstrap_period=cfg.bootstrap_period,refresh_period=cfg.refresh_period,min_candles=cfg.min_candles,max_cache_rows=cfg.max_cache_rows,timeout_seconds=cfg.network_timeout_seconds,max_retries=cfg.max_retries,backoff_seconds=cfg.retry_backoff_seconds)
 economics=EconomicsProviderChain([WebullEconomicsProvider(cfg.credentials),ManualEconomicsProvider(root/args.manual_economics),ProxyEconomicsProvider()])
 # Rebuild crossing state from immutable observations after restart.
 crossings={}; last_side={}
 for o in store.read("observations"):
  key=(o.get("contract_id"),o.get("asset")); crossings[key]=max(crossings.get(key,0),int(o.get("reference_crossings",0))); last_side[key]=float(o.get("buffer",0))>=0
 print("MODE: OBSERVATION_ONLY | NO AUTOMATED EXECUTION")
 print("WEBULL_STATUS = "+WebullEconomicsProvider(cfg.credentials).status)
 while True:
  instant=clock.capture(); window=ContractWindow.for_instant(instant,tz,cfg.contract_window_minutes)
  for asset in cfg.active_assets:
   started=time.monotonic(); bars=market.get_bars(asset,instant); latency=time.monotonic()-started
   if bars is None or bars.is_empty: continue
   frame=bars.frame; visible=frame[frame.index<instant.utc]
   opening=visible[visible.index>=window.start_utc]
   if opening.empty: continue
   reference=float(opening.iloc[0]["Open"]); spot=float(visible.iloc[-1]["Close"]); key=(window.contract_id,asset); side=spot>=reference
   if key in last_side and side!=last_side[key]: crossings[key]=crossings.get(key,0)+1
   last_side[key]=side; ind=precompute_indicators(visible); sigma1=float(ind.realized_vol_1m.iloc[-1]); seconds=window.seconds_remaining(instant); sigma_rem=sigma1*math.sqrt(max(seconds/60,1e-12)); buffer_pct=(spot-reference)/reference
   anchor=gaussian_terminal(np.array([buffer_pct]),np.array([sigma_rem]),spot=np.array([spot]),reference=np.array([reference])); heavy=student_t_terminal(np.array([buffer_pct]),np.array([sigma_rem]),nu=4.456245976114701,spot=np.array([spot])); channels={"gaussian":anchor.p_yes,"student_t":heavy.p_yes}; agreement=combine_channels(channels); lower=conservative_lower_bound(channels)
   sens=digital_sensitivities(spot=np.array([spot]),reference=np.array([reference]),seconds_remaining=np.array([seconds]),sigma_1m=np.array([sigma1])); scaled=scaled_sensitivities(sens,np.array([spot]),np.array([sigma1])); returns=np.diff(np.log(visible["Close"].astype(float).to_numpy())); rv5=float(np.std(returns[-5:],ddof=1)) if len(returns)>=5 else sigma1; rv30=float(np.std(returns[-30:],ddof=1)) if len(returns)>=30 else sigma1; vov=np.array([abs(math.log(rv5/rv30)) if rv5>0 and rv30>0 else 0.]); frag=compute_fragility(gamma_per_pct2=scaled["gamma_per_pct2"],vega_per_10pct_vol=scaled["vega_per_10pct_vol"],theta_per_30s=scaled["theta_per_30s"],abs_z=np.abs(sens.z),seconds_remaining=np.array([seconds]),crossings=np.array([crossings.get(key,0)]),vol_of_vol=vov,disagreement=agreement.disagreement,scales=PHASE6_FRAGILITY_SCALES)
   age=max(0.,(instant.utc-(bars.last_timestamp)).total_seconds()-60) if bars.last_timestamp else float("inf"); econ=economics.get_economics(asset,window.contract_id,instant.utc)
   state=LiveAssetState(asset,window.contract_id,window.start_utc,window.end_utc,instant.utc,reference,ReferenceStatus.PROXY,spot,sigma_rem,float(anchor.p_yes[0]),float(lower[0]),float(frag.score[0]),float(agreement.disagreement[0]),float(anchor.p_cross_reference[0]),crossings.get(key,0),age,latency,provider_mode=economics.last_provider or "proxy",economics=econ,quality_ok=np.isfinite(sigma1) and sigma1>0,quality_reason=market.health.display)
   snap=engine.process(state); print(render_snapshot(snap)); print("-"*78)
   for pending in [x for x in store.unresolved_entries() if x.get("asset")==asset]:
    observations=[o for o in store.read("observations") if o.get("contract_id")==pending["contract_id"] and o.get("asset")==asset]
    if not observations: continue
    end=datetime.fromisoformat(observations[0]["window_end"])
    if end>instant.utc: continue
    terminal_rows=visible[visible.index>=end]
    if terminal_rows.empty: continue
    engine.resolve(asset=asset,contract_id=pending["contract_id"],resolution_timestamp=end,terminal_value=float(terminal_rows.iloc[0]["Open"]),reference=float(pending["reference"]),resolution_source="PROXY_UNDERLYING",verified=False)
  store.append("provider_health",{"health_id":str(uuid.uuid4()),"timestamp_utc":instant.utc.isoformat(),"provider":"yahoo","state":market.health.state.value,"detail":market.health.detail,"successful_fetches":market.health.total_requests-market.health.total_failures,"failed_fetches":market.health.total_failures},"health_id")
  if args.once: break
  elapsed=clock.monotonic()-instant.monotonic; clock.sleep(max(0,cfg.scan_interval_seconds-elapsed))
if __name__=="__main__": main()
