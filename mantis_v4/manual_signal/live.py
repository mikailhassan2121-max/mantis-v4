"""Anonymous live data acquisition for Step 10 experimental manual signals."""
from __future__ import annotations

import hashlib
import gc
import json
import subprocess
import uuid
from collections import Counter, deque
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

from .core import (ACTIONABILITY, ASSETS, V2_POLICY, V21_POLICY, V22_POLICY, FeeMetadataVerifier,
    ManualSignalStore, apply_signal_lock, evaluate_candidate, select_primary_signal)
from .telemetry import PolicyTelemetryStore, policy_observation

RECENT_SHADOW_ROWS = 4096


def _placeholder(asset, status, reason, seconds_remaining):
    return {"asset":asset,"side":None,"confidence":None,"conservative_probability":None,
        "fragility":None,"disagreement":None,"crossing_probability":None,"crossings":None,
        "seconds_remaining":seconds_remaining,"status":status,"reason":reason,
        "policy_version":V22_POLICY.version,"event_market_provider":"KALSHI_PUBLIC_REST",
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
        self.shadow=ShadowStore(shadow_dir,recent_id_limit=RECENT_SHADOW_ROWS)
        self.signals=ManualSignalStore(signal_dir,policy_version=V22_POLICY.version)
        self.policy_telemetry=PolicyTelemetryStore(signal_dir)
        self.crossings={}; self.last_side={}
        self.locked_signal=None; self.lock_window_end=None
        self.completed_windows=0; self.windows_with_signal=0; self.windows_without_signal=0
        self.signals_by_asset=Counter({asset:0 for asset in ASSETS}); self.signals_by_side=Counter({"YES":0,"NO":0})
        self.issue_times=deque(maxlen=512); self.blocking_gates=Counter()
        self._window_had_signal=False; self._window_v2=False; self._window_v21=False; self._window_v22=False
        self.v2_signal_windows=0; self.v21_signal_windows=0; self.v22_signal_windows=0
        self.memory_status="NORMAL"
        try:self.git_commit=subprocess.check_output(["git","rev-parse","HEAD"],cwd=root,text=True,stderr=subprocess.DEVNULL).strip()
        except Exception:self.git_commit="UNKNOWN"
        self.shadow.append("runs",_shadow_run(self.run_id,"START",datetime.now(UTC),self.git_commit))

    def close(self):
        self.shadow.append("runs",_shadow_run(self.run_id,"STOP",datetime.now(UTC),self.git_commit))

    def scan(self, instant, window, *, persist_signal=True):
        resolve_available(self.shadow,self.provider,self.run_id,instant.utc,recent_limit=RECENT_SHADOW_ROWS)
        window_end=window.end_utc.isoformat()
        self.policy_telemetry.complete_before(window.start_utc.isoformat(),(V2_POLICY,V21_POLICY,V22_POLICY))
        if self.lock_window_end != window_end:
            if self.lock_window_end is not None:
                self.completed_windows+=1
                self.windows_with_signal+=int(self._window_had_signal)
                self.windows_without_signal+=int(not self._window_had_signal)
                self.v2_signal_windows+=int(self._window_v2); self.v21_signal_windows+=int(self._window_v21)
                self.v22_signal_windows+=int(self._window_v22)
            self.lock_window_end=window_end
            self.locked_signal=self.signals.locked_for_window(window_end)
            self._window_had_signal=bool(self.locked_signal); self._window_v2=False; self._window_v21=False; self._window_v22=False
            current_keys={(window.contract_id,asset) for asset in ASSETS}
            self.crossings={key:value for key,value in self.crossings.items() if key in current_keys}
            self.last_side={key:value for key,value in self.last_side.items() if key in current_keys}
        candidates=[]; v2_candidates=[]; v21_candidates=[]; ui=[]; contexts=[]
        for asset in ASSETS:
            result=self.provider.get_quote(asset,window.start_utc,window.end_utc,instant.utc)
            snap=None; metadata=None
            if result.mapping is None:
                status="MARKET INITIALIZING" if result.status is KalshiStatus.MARKET_INITIALIZING else "QUOTE UNAVAILABLE"
                placeholder=_placeholder(asset,status,result.status.value,window.seconds_remaining(instant))
                placeholder.update(window_start_utc=window.start_utc.isoformat(),window_end_utc=window.end_utc.isoformat())
                candidates.append(placeholder); v2_candidates.append(dict(placeholder)); v21_candidates.append(dict(placeholder))
                contexts.append((asset,None,None,result.quote,result.status.value,None)); continue
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
                    dev_p50_bps=self.bounds[asset]["P50"],fee_metadata=metadata,now=instant.utc,policy=V22_POLICY)
                v21_candidate=evaluate_candidate(snapshot=snap,mapping=result.mapping,quote=result.quote,
                    quote_status=result.status.value,dev_p95_bps=self.bounds[asset]["P95"],
                    fee_metadata=metadata,now=instant.utc,policy=V21_POLICY)
                v2_candidate=evaluate_candidate(snapshot=snap,mapping=result.mapping,quote=result.quote,
                    quote_status=result.status.value,dev_p95_bps=self.bounds[asset]["P95"],
                    fee_metadata=metadata,now=instant.utc,policy=V2_POLICY)
                candidates.append(candidate); ui.append(self._ui_snapshot(snap,result,candidate,shadow_observation["observation_id"]))
                v2_candidates.append(v2_candidate); v21_candidates.append(v21_candidate)
                contexts.append((asset,snap,result.mapping,result.quote,result.status.value,metadata))
            except Exception as exc:
                placeholder=_placeholder(asset,"NO SIGNAL",type(exc).__name__,window.seconds_remaining(instant))
                placeholder.update(window_start_utc=window.start_utc.isoformat(),window_end_utc=window.end_utc.isoformat())
                candidates.append(placeholder); v2_candidates.append(dict(placeholder)); v21_candidates.append(dict(placeholder))
                contexts.append((asset,snap,result.mapping,result.quote,result.status.value,metadata))
        v2_selection=select_primary_signal(v2_candidates,policy=V2_POLICY)
        v21_selection=select_primary_signal(v21_candidates,policy=V21_POLICY)
        selection=select_primary_signal(candidates,policy=V22_POLICY)
        current={row["asset"]:row for row in candidates}; prior={row["asset"]:row for row in v2_candidates}
        prior21={row["asset"]:row for row in v21_candidates}
        telemetry=[]
        context_by_asset={row[0]:row for row in contexts}
        for asset in ASSETS:
            context=context_by_asset.get(asset,(asset,None,None,None,"QUOTE_UNAVAILABLE",None))
            for policy,row,chosen in ((V2_POLICY,prior[asset],v2_selection.get("selected")),
                                      (V21_POLICY,prior21[asset],v21_selection.get("selected")),
                                      (V22_POLICY,current[asset],selection.get("selected"))):
                record=policy_observation(policy=policy,snapshot=context[1],mapping=context[2],quote=context[3],
                    quote_status=context[4],fee_metadata=context[5],now=instant.utc,
                    dev_p95_bps=self.bounds.get(asset,{}).get("P95"),final_candidate=row)
                record["policy_selected_this_scan"]=bool(chosen and chosen.get("asset")==asset)
                telemetry.append(record)
        self.policy_telemetry.append_scan(telemetry)
        self._window_v2|=bool(v2_selection["selected"]); self._window_v21|=bool(v21_selection["selected"])
        self._window_v22|=bool(selection["selected"])
        for row in candidates:
            for gate in row.get("blocking_gates",[]): self.blocking_gates[gate["gate"]]+=1
            if row.get("status") not in {"ELIGIBLE","PRIMARY"} and not row.get("blocking_gates"):
                self.blocking_gates[row.get("status","UNKNOWN")]+=1
        was_locked=self.locked_signal is not None
        selection,self.locked_signal=apply_signal_lock(selection,self.locked_signal,instant.utc,policy=V22_POLICY)
        if not was_locked and self.locked_signal is not None:
            self._window_had_signal=True
            self.signals_by_asset[self.locked_signal["asset"]]+=1; self.signals_by_side[self.locked_signal["side"]]+=1
            if self.locked_signal.get("seconds_remaining") is not None:self.issue_times.append(float(self.locked_signal["seconds_remaining"]))
        from mantis_v4.ui.hostmetrics import host_payload
        memory=host_payload(); self.memory_status=memory.get("memory_status","NORMAL")
        selection["operator_state"]["memory_status"]=self.memory_status
        selection["operator_state"]["buffer_counts"]={"crossings":len(self.crossings),
            "last_side":len(self.last_side),"fee_metadata":len(self.fees._cache),
            "market_mappings":len(self.provider._mapping_cache)}
        selection["operator_state"]["forward_shadow_summary"]=self.shadow.summary()
        selection["operator_state"]["signal_frequency"]=self.frequency_summary()
        selection["operator_state"]["policy_comparison"]={"v2_would_signal_current_window":self._window_v2,
            "v21_would_signal_current_window":self._window_v21,"v2_signal_windows":self.v2_signal_windows,
            "v21_signal_windows":self.v21_signal_windows,"v22_would_signal_current_window":self._window_v22,
            "v22_signal_windows":self.v22_signal_windows}
        selection["operator_state"]["policy_sample"]=self.policy_telemetry.summary(V2_POLICY,V21_POLICY,V22_POLICY)
        if self.memory_status in {"WARNING","CRITICAL"}:
            self.provider.invalidate(); gc.collect()
        persisted=False
        if selection["selected"] and persist_signal and selection["selected"].get("issued_at_utc")==instant.utc.isoformat():
            persisted=self.signals.append(selection["selected"],instant.utc)
        return ui,selection,persisted

    def frequency_summary(self):
        ordered=sorted(self.issue_times)
        median=(ordered[len(ordered)//2] if ordered else None)
        return {"completed_windows":self.completed_windows,"windows_with_signal":self.windows_with_signal,
            "windows_without_signal":self.windows_without_signal,
            "signal_rate":self.windows_with_signal/self.completed_windows if self.completed_windows else None,
            "signals_by_asset":dict(self.signals_by_asset),"signals_by_side":dict(self.signals_by_side),
            "average_seconds_remaining_at_issue":sum(self.issue_times)/len(self.issue_times) if self.issue_times else None,
            "median_seconds_remaining_at_issue":median,
            "most_common_blocking_gate":self.blocking_gates.most_common(1)[0][0] if self.blocking_gates else None,
            "issue_timing_samples":len(self.issue_times)}

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
            reference_crossings=snap.reference_crossings,phase6_state="QUALIFIED" if candidate["status"]!="CONF FAIL" else "WAIT",
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
