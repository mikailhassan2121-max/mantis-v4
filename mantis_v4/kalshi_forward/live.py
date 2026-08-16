"""Dedicated live runner for Step 9 shadow collection only."""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from mantis_v4.backtest.features import precompute_indicators
from mantis_v4.clock import Clock, load_timezone
from mantis_v4.contracts import ContractWindow
from mantis_v4.economics.kalshi import KalshiEventMarketProvider, KalshiStatus
from mantis_v4.providers.market_data import YFinanceMarketDataProvider
from mantis_v4.simulation import (combine_channels, compute_fragility,
    conservative_lower_bound, digital_sensitivities, gaussian_terminal,
    scaled_sensitivities, student_t_terminal)

from .core import (ACTIONABILITY, ASSETS, MODEL_POLICY, REFERENCE_POLICY,
    SAMPLE_LABEL, SCHEMA_VERSION, ShadowStore, audit_store, build_observation,
    build_resolution, shadow_report)

FRAGILITY_SCALES = {"gamma":7.537633538712959,"vega":0.013047089075600496,
                    "theta":0.004530053560656391,"vol_of_vol":0.3016632827895451}


def load_empirical_bounds(root: Path) -> dict[str, dict[str, Decimal]]:
    path = Path(root) / "data" / "kalshi_step8_reference_risk_summary.json"
    try: payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc: raise RuntimeError("frozen Step 8 bounds unavailable") from exc
    if payload.get("policy_version") != REFERENCE_POLICY or payload.get("production_authorization") is not False:
        raise RuntimeError("Step 8 policy identity mismatch")
    raw = payload.get("bounds_fitted_on_development_only")
    if set(raw or {}) != set(ASSETS): raise RuntimeError("Step 8 four-asset bounds incomplete")
    return {asset: {q: Decimal(str(raw[asset][q])) for q in ("P50","P95","P99")} for asset in ASSETS}


def compute_shadow_snapshot(*, asset, bars, instant, window, target: Decimal,
                            crossings: dict, last_side: dict):
    """Frozen Normal-Z machinery evaluated against the actual Kalshi target.

    This is isolated from the historical/live production function so the old
    Yahoo-open policy remains byte-for-byte unchanged.
    """
    visible = bars.frame[bars.frame.index < instant.utc]
    if visible.empty: return None
    reference=float(target); spot=float(visible.iloc[-1]["Close"]); key=(window.contract_id,asset)
    side=spot>=reference
    if key in last_side and side != last_side[key]: crossings[key]=crossings.get(key,0)+1
    last_side[key]=side
    ind=precompute_indicators(visible); sigma1=float(ind.realized_vol_1m.iloc[-1])
    if not np.isfinite(sigma1) or sigma1 <= 0: return None
    seconds=window.seconds_remaining(instant); sigma_rem=sigma1*math.sqrt(max(seconds/60,1e-12))
    buffer_pct=(spot-reference)/reference
    anchor=gaussian_terminal(np.array([buffer_pct]),np.array([sigma_rem]),spot=np.array([spot]),reference=np.array([reference]))
    heavy=student_t_terminal(np.array([buffer_pct]),np.array([sigma_rem]),nu=4.456245976114701,spot=np.array([spot]))
    channels={"gaussian":anchor.p_yes,"student_t":heavy.p_yes}; agreement=combine_channels(channels)
    lower=conservative_lower_bound(channels)
    sens=digital_sensitivities(spot=np.array([spot]),reference=np.array([reference]),seconds_remaining=np.array([seconds]),sigma_1m=np.array([sigma1]))
    scaled=scaled_sensitivities(sens,np.array([spot]),np.array([sigma1]))
    returns=np.diff(np.log(visible["Close"].astype(float).to_numpy()))
    rv5=float(np.std(returns[-5:],ddof=1)) if len(returns)>=5 else sigma1
    rv30=float(np.std(returns[-30:],ddof=1)) if len(returns)>=30 else sigma1
    vov=np.array([abs(math.log(rv5/rv30)) if rv5>0 and rv30>0 else 0.])
    frag=compute_fragility(gamma_per_pct2=scaled["gamma_per_pct2"],vega_per_10pct_vol=scaled["vega_per_10pct_vol"],
        theta_per_30s=scaled["theta_per_30s"],abs_z=np.abs(sens.z),seconds_remaining=np.array([seconds]),
        crossings=np.array([crossings.get(key,0)]),vol_of_vol=vov,disagreement=agreement.disagreement,scales=FRAGILITY_SCALES)
    p=float(anchor.p_yes[0])
    return SimpleNamespace(asset=asset,timestamp_utc=instant.utc.isoformat(),current_price=spot,
        p_yes=p,p_no=1-p,predicted_side="YES" if p>=.5 else "NO",conservative_bound=float(lower[0]),
        fragility=float(frag.score[0]),disagreement=float(agreement.disagreement[0]),
        crossing_probability=float(anchor.p_cross_reference[0]),reference_crossings=crossings.get(key,0),
        seconds_remaining=seconds,volatility_estimate=sigma_rem)


def _run_record(run_id, event, instant, git_commit="UNKNOWN"):
    return {"run_record_id":f"{run_id}:{event}","schema_version":SCHEMA_VERSION,
        "policy_version":REFERENCE_POLICY,"model_policy":MODEL_POLICY,"reference_policy":REFERENCE_POLICY,
        "actionability":ACTIONABILITY,"sample_label":SAMPLE_LABEL,"run_id":run_id,
        "contract_id":"SYSTEM","asset":"SYSTEM","series_ticker":None,"event_ticker":None,
        "market_ticker":None,"window_start_utc":None,"window_end_utc":None,
        "observed_at_utc":instant.isoformat(),"event":event,"git_commit":git_commit,
        "asset_universe":list(ASSETS),"provider_mode":"YAHOO_PROXY+KALSHI_PUBLIC_REST",
        "authenticated":False,"production_authorization":False}


def _health_record(run_id, asset, window, instant, status, detail=""):
    contract=f"{asset}|{window.start_utc.isoformat()}|{window.end_utc.isoformat()}|15m"
    return {"provider_health_id":hashlib.sha256(f"{run_id}|{asset}|{window.end_utc}|{int(window.seconds_remaining(instant))}|{status}".encode()).hexdigest(),
        "schema_version":SCHEMA_VERSION,"policy_version":REFERENCE_POLICY,"model_policy":MODEL_POLICY,
        "reference_policy":REFERENCE_POLICY,"actionability":ACTIONABILITY,"sample_label":SAMPLE_LABEL,
        "run_id":run_id,"contract_id":contract,"asset":asset,"series_ticker":None,"event_ticker":None,
        "market_ticker":None,"window_start_utc":window.start_utc.isoformat(),"window_end_utc":window.end_utc.isoformat(),
        "observed_at_utc":instant.utc.isoformat(),"provider":"KALSHI_PUBLIC_REST","status":status,"detail":detail}


def resolve_available(store: ShadowStore, provider: KalshiEventMarketProvider, run_id: str, now: datetime) -> int:
    resolved = {r["contract_id"] for r in store.read("resolutions")}; count=0
    first_by_contract={}
    for observation in store.read("observations"): first_by_contract.setdefault(observation["contract_id"],observation)
    for contract_id, observation in first_by_contract.items():
        if contract_id in resolved or datetime.fromisoformat(observation["window_end_utc"]) > now: continue
        try:
            market=provider.client.get("/markets/"+observation["market_ticker"]).get("market")
            if not isinstance(market,dict) or str(market.get("result") or "").lower() not in {"yes","no"}: continue
            record=build_resolution(run_id=run_id,observation=observation,market=market,resolved_at=now)
            count += int(store.append("resolutions",record))
        except Exception:
            continue
    return count


def run_live_shadow(*, root: Path, output: Path, config, once: bool=False,
                    provider=None, market=None, clock=None) -> dict:
    store=ShadowStore(output); bounds=load_empirical_bounds(root)
    provider=provider or KalshiEventMarketProvider(); clock=clock or Clock()
    market=market or YFinanceMarketDataProvider(interval=config.bar_interval,bootstrap_period=config.bootstrap_period,
        refresh_period=config.refresh_period,min_candles=config.min_candles,max_cache_rows=config.max_cache_rows,
        timeout_seconds=config.network_timeout_seconds,max_retries=config.max_retries,
        backoff_seconds=config.retry_backoff_seconds,cooldown_seconds=config.provider_cooldown_seconds)
    run_id=str(uuid.uuid4())
    try: git_commit=subprocess.check_output(["git","rev-parse","HEAD"],cwd=root,text=True,stderr=subprocess.DEVNULL).strip()
    except Exception: git_commit="UNKNOWN"
    store.append("runs",_run_record(run_id,"START",datetime.now(UTC),git_commit))
    crossings={}; last_side={}; scans=0; observations=0; attempts=0
    print("KALSHI FORWARD SHADOW")
    print("ACTIONABILITY ........... SHADOW ONLY")
    print("AUTHENTICATION .......... NONE")
    try:
        while True:
            instant=clock.capture(); tz=load_timezone(config.contract_timezone)
            window=ContractWindow.for_instant(instant,tz,config.contract_window_minutes)
            resolve_available(store,provider,run_id,instant.utc)
            for asset in ASSETS:
                attempts += 1
                result=provider.get_quote(asset,window.start_utc,window.end_utc,instant.utc)
                if result.mapping is None:
                    store.append("provider_health",_health_record(run_id,asset,window,instant,result.status.value,result.detail)); continue
                if result.status not in {KalshiStatus.READY,KalshiStatus.BOOK_SIDE_MISSING}:
                    store.append("provider_health",_health_record(run_id,asset,window,instant,result.status.value,result.detail)); continue
                try:
                    bars=market.get_bars(asset,instant)
                    if bars is None or bars.is_empty: raise RuntimeError("UNDERLYING_DATA_UNAVAILABLE")
                    snap=compute_shadow_snapshot(asset=asset,bars=bars,instant=instant,window=window,target=result.mapping.target,
                                                 crossings=crossings,last_side=last_side)
                    if snap is None: raise RuntimeError("INSUFFICIENT_CAUSAL_HISTORY")
                    observation,candidate=build_observation(run_id=run_id,snapshot=snap,mapping=result.mapping,
                        quote=result.quote,empirical_bounds=bounds)
                    observations += int(store.append("observations",observation))
                    store.append("shadow_candidates",candidate)
                    store.append("provider_health",_health_record(run_id,asset,window,instant,"READY"))
                except Exception as exc:
                    store.append("provider_health",_health_record(run_id,asset,window,instant,"DEGRADED",type(exc).__name__))
            scans += 1
            if once: break
            elapsed=clock.monotonic()-instant.monotonic; clock.sleep(max(0,config.scan_interval_seconds-elapsed))
    except KeyboardInterrupt:
        pass
    finally:
        store.append("runs",_run_record(run_id,"STOP",datetime.now(UTC),git_commit))
    audit=audit_store(store)
    result={"run_id":run_id,"scan_cycles":scans,"assets_attempted":attempts,"observations_created":observations,
            "resolutions_created":len(store.read("resolutions")),"audit":audit,"report":shadow_report(store),
            "actionable_selections":0,"actionable_voice_events":0,"orders_submitted":0}
    print(json.dumps(result,indent=2,sort_keys=True)); return result
