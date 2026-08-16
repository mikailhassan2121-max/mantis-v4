"""Anonymous live data acquisition for Step 10 experimental manual signals."""
from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

from mantis_v4.economics.kalshi import KalshiEventMarketProvider, KalshiStatus
from mantis_v4.forward.models import LiveSnapshot
from mantis_v4.kalshi_forward.core import (ACTIONABILITY as SHADOW_ACTIONABILITY,
    MODEL_POLICY, REFERENCE_POLICY as SHADOW_POLICY, SAMPLE_LABEL, SCHEMA_VERSION,
    ShadowStore, build_observation)
from mantis_v4.kalshi_forward.live import (compute_shadow_snapshot,
    load_empirical_bounds, resolve_available)
from mantis_v4.providers.market_data import YFinanceMarketDataProvider

from .core import (ACTIONABILITY, ASSETS, POLICY_VERSION, FeeMetadataVerifier,
    ManualSignalStore, evaluate_candidate, select_primary_signal)


def _placeholder(asset, status, reason, seconds_remaining):
    return {"asset":asset,"side":None,"confidence":None,"conservative_probability":None,
        "fragility":None,"disagreement":None,"crossing_probability":None,"crossings":None,
        "seconds_remaining":seconds_remaining,"status":status,"reason":reason,
        "policy_version":POLICY_VERSION,"event_market_provider":"KALSHI_PUBLIC_REST",
        "manual_only":True,"production_validated":False,
        "authentication":"NONE","order_capability":"DISABLED","economically_valid":False}


def _shadow_run(run_id, event, now, git_commit):
    return {"run_record_id":f"{run_id}:{event}","schema_version":SCHEMA_VERSION,
        "policy_version":SHADOW_POLICY,"model_policy":MODEL_POLICY,"reference_policy":SHADOW_POLICY,
        "actionability":SHADOW_ACTIONABILITY,"sample_label":SAMPLE_LABEL,"run_id":run_id,
        "contract_id":"SYSTEM","asset":"SYSTEM","series_ticker":None,"event_ticker":None,
        "market_ticker":None,"window_start_utc":None,"window_end_utc":None,
        "observed_at_utc":now.astimezone(UTC).isoformat(),"event":event,"git_commit":git_commit,
        "asset_universe":list(ASSETS),"provider_mode":"YAHOO_PROXY+KALSHI_PUBLIC_REST",
        "authenticated":False,"production_authorization":False,"collector":"EXPERIMENTAL_MANUAL_SIGNAL_MODE"}


class ManualSignalEngine:
    """Own one scan cycle; no UI, voice, account, or execution dependency."""
    def __init__(self, *, root: Path, config, shadow_dir: Path, signal_dir: Path,
                 provider=None, market=None):
        self.root=Path(root); self.config=config; self.run_id=str(uuid.uuid4())
        self.provider=provider or KalshiEventMarketProvider()
        self.market=market or YFinanceMarketDataProvider(interval=config.bar_interval,
            bootstrap_period=config.bootstrap_period,refresh_period=config.refresh_period,
            min_candles=config.min_candles,max_cache_rows=config.max_cache_rows,
            timeout_seconds=config.network_timeout_seconds,max_retries=config.max_retries,
            backoff_seconds=config.retry_backoff_seconds,cooldown_seconds=config.provider_cooldown_seconds)
        self.fees=FeeMetadataVerifier(self.provider.client); self.bounds=load_empirical_bounds(root)
        self.shadow=ShadowStore(shadow_dir); self.signals=ManualSignalStore(signal_dir)
        self.crossings={}; self.last_side={}
        try:self.git_commit=subprocess.check_output(["git","rev-parse","HEAD"],cwd=root,text=True,stderr=subprocess.DEVNULL).strip()
        except Exception:self.git_commit="UNKNOWN"
        self.shadow.append("runs",_shadow_run(self.run_id,"START",datetime.now(UTC),self.git_commit))

    def close(self):
        self.shadow.append("runs",_shadow_run(self.run_id,"STOP",datetime.now(UTC),self.git_commit))

    def scan(self, instant, window, *, persist_signal=True):
        resolve_available(self.shadow,self.provider,self.run_id,instant.utc)
        candidates=[]; ui=[]
        for asset in ASSETS:
            result=self.provider.get_quote(asset,window.start_utc,window.end_utc,instant.utc)
            if result.mapping is None:
                status="MARKET INITIALIZING" if result.status is KalshiStatus.MARKET_INITIALIZING else "QUOTE UNAVAILABLE"
                candidates.append(_placeholder(asset,status,result.status.value,window.seconds_remaining(instant))); continue
            try:
                bars=self.market.get_bars(asset,instant)
                if bars is None or bars.is_empty: raise RuntimeError("UNDERLYING DATA UNAVAILABLE")
                snap=compute_shadow_snapshot(asset=asset,bars=bars,instant=instant,window=window,
                    target=result.mapping.target,crossings=self.crossings,last_side=self.last_side)
                if snap is None: raise RuntimeError("INSUFFICIENT CAUSAL HISTORY")
                shadow_observation,shadow_candidate=build_observation(run_id=self.run_id,snapshot=snap,
                    mapping=result.mapping,quote=result.quote,empirical_bounds=self.bounds)
                self.shadow.append("observations",shadow_observation)
                self.shadow.append("shadow_candidates",shadow_candidate)
                metadata=self.fees.verify(asset)
                candidate=evaluate_candidate(snapshot=snap,mapping=result.mapping,quote=result.quote,
                    quote_status=result.status.value,dev_p95_bps=self.bounds[asset]["P95"],
                    fee_metadata=metadata,now=instant.utc)
                candidates.append(candidate); ui.append(self._ui_snapshot(snap,result,candidate,shadow_observation["observation_id"]))
            except Exception as exc:
                candidates.append(_placeholder(asset,"NO SIGNAL",type(exc).__name__,window.seconds_remaining(instant)))
        selection=select_primary_signal(candidates)
        persisted=False
        if selection["selected"] and persist_signal:
            persisted=self.signals.append(selection["selected"],instant.utc)
        return ui,selection,persisted

    def _ui_snapshot(self,snap,result,candidate,observation_id):
        mapping=result.mapping; quote=result.quote; now=snap.timestamp_utc
        return LiveSnapshot(run_id=self.run_id,observation_id=observation_id,timestamp_utc=now,
            timestamp_local=now,asset=snap.asset,contract_id=candidate["contract_id"],
            window_start=mapping.window_start_utc.isoformat(),window_end=mapping.window_end_utc.isoformat(),
            seconds_remaining=snap.seconds_remaining,reference=float(mapping.target),
            reference_status="OFFICIAL_VERIFIED_REFERENCE",current_price=snap.current_price,
            buffer=snap.current_price-float(mapping.target),volatility_estimate=snap.volatility_estimate,
            p_yes=snap.p_yes,p_no=snap.p_no,predicted_side=snap.predicted_side,
            conservative_bound=snap.conservative_bound,fragility=snap.fragility,
            disagreement=snap.disagreement,crossing_probability=snap.crossing_probability,
            reference_crossings=snap.reference_crossings,phase6_state="QUALIFIED" if candidate["status"] not in {"CONF FAIL","WAIT T-300"} else "WAIT",
            phase6_reason=candidate["reason"],final_decision="WAIT",final_reason=candidate["status"],
            data_age_seconds=0,fetch_latency_seconds=0,next_scan_utc=now,
            yes_bid=float(quote.yes_bid) if quote and quote.yes_bid is not None else None,
            yes_ask=float(quote.yes_ask) if quote and quote.yes_ask is not None else None,
            no_bid=float(quote.no_bid) if quote and quote.no_bid is not None else None,
            no_ask=float(quote.no_ask) if quote and quote.no_ask is not None else None,
            quote_timestamp=quote.received_at_utc.isoformat() if quote else None,
            quote_age=float(quote.quote_age_seconds) if quote else None,
            quote_status=result.status.value,metadata={"experimental_manual_signal":candidate})


def render_manual_diagnostics(selection: dict) -> str:
    """Human-readable one-scan Step 10 state; never invokes legacy selection."""
    lines=["KALSHI EXPERIMENTAL MANUAL SIGNAL DIAGNOSTICS"]
    for row in selection["candidates"]:
        lines.extend(["",row["asset"],
            f"WINDOW .................. {row.get('window_start_utc') or '--'} -> {row.get('window_end_utc') or '--'}",
            f"SECONDS REMAINING ....... {row.get('seconds_remaining') if row.get('seconds_remaining') is not None else '--'}",
            f"KALSHI TICKER ........... {row.get('market_ticker') or '--'}",
            f"KALSHI TARGET ........... {row.get('target') or '--'}",
            f"PROXY CURRENT ........... {row.get('proxy_current') or '--'}",
            f"MODEL SIDE / PROB ....... {row.get('side') or '--'} / {row.get('confidence') if row.get('confidence') is not None else '--'}",
            f"LCB ..................... {row.get('conservative_probability') if row.get('conservative_probability') is not None else '--'}",
            f"REFERENCE DISTANCE ...... {row.get('distance_bps') or '--'} bp",
            f"DEV_P95 STATUS .......... {row.get('reference_risk') or 'REFERENCE_UNKNOWN'}",
            f"YES / NO ASK ............ {row.get('yes_ask') or '--'} / {row.get('no_ask') or '--'}",
            f"NEEDED ASK / AGE ........ {row.get('needed_ask') or '--'} / {row.get('quote_age_seconds') or '--'}",
            f"FEE ..................... {row.get('fee') or '--'} ({row.get('fee_provenance') or 'UNVERIFIED'})",
            f"NET / CONS EV ........... {row.get('net_ev') or '--'} / {row.get('conservative_net_ev') or '--'}",
            f"CANDIDATE STATUS ........ {row.get('status')} — {row.get('reason')}"])
    lines.extend(["", "MANUAL_SIGNAL_STATE "+json.dumps(selection["operator_state"],sort_keys=True,default=str)])
    return "\n".join(lines)
