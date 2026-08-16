"""Prospective, descriptive V2/V2.1/V2.2 policy telemetry.

This module records what the two frozen manual policies saw on the same scan.
It cannot place an order, change a candidate, or influence the operator lock.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from collections import Counter, deque
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from .core import (ASSETS, MAX_TOTAL_ENTRY_COST, ONE, KalshiFeeModel,
                   ManualSignalPolicy, quality_gate_failures)

SCHEMA_VERSION="MANUAL_POLICY_TELEMETRY_V1"
SAMPLE_LABEL="DESCRIPTIVE_FORWARD_SAMPLE_NOT_PERFORMANCE_VALIDATION"
POLICIES_PER_WINDOW=3
RECENT_IDS=4096


def _iso(value):
    return value.astimezone(UTC).isoformat() if isinstance(value,datetime) else value


def _dec(value):
    if value is None:return None
    result=Decimal(str(value))
    return result if result.is_finite() else None


def _gate(name, passed, value=None, threshold=None, operator=None, blocking=True):
    return {"gate":name,"passed":bool(passed),"value":str(value) if value is not None else None,
            "threshold":str(threshold) if threshold is not None else None,"operator":operator,
            "blocking":bool(blocking)}


def policy_observation(*, policy: ManualSignalPolicy, snapshot, mapping, quote,
                       quote_status: str, fee_metadata, now: datetime,
                       dev_p95_bps, final_candidate: dict) -> dict:
    """Build a complete all-gates record without changing fail-fast signal logic."""
    asset=final_candidate.get("asset") or getattr(snapshot,"asset",None)
    window_start=final_candidate.get("window_start_utc") or _iso(getattr(mapping,"window_start_utc",None))
    window_end=final_candidate.get("window_end_utc") or _iso(getattr(mapping,"window_end_utc",None))
    side=final_candidate.get("side") or getattr(snapshot,"predicted_side",None)
    ask=(quote.yes_ask if side=="YES" else quote.no_ask) if quote is not None and side in {"YES","NO"} else None
    ask_size=(quote.yes_ask_size if side=="YES" else quote.no_ask_size) if quote is not None and side in {"YES","NO"} else None
    quality={g["gate"]:False for g in quality_gate_failures(snapshot,policy)} if snapshot is not None else {}
    gates=[]
    metrics=(("CONFIDENCE",max(snapshot.p_yes,snapshot.p_no),policy.confidence,">="),
             ("CONSERVATIVE",snapshot.conservative_bound,policy.conservative_probability,">="),
             ("FRAGILITY",snapshot.fragility,policy.fragility,"<="),
             ("DISAGREEMENT",snapshot.disagreement,policy.disagreement,"<="),
             ("CROSSING PROBABILITY",snapshot.crossing_probability,policy.crossing_probability,"<="),
             ("CROSSINGS",snapshot.reference_crossings,policy.crossings,"<=")) if snapshot is not None else ()
    for name,value,threshold,operator in metrics:
        gates.append(_gate(name,name not in quality,value,threshold,operator))
    if snapshot is None:
        for name,threshold,operator in (("CONFIDENCE",policy.confidence,">="),
                ("CONSERVATIVE",policy.conservative_probability,">="),("FRAGILITY",policy.fragility,"<="),
                ("DISAGREEMENT",policy.disagreement,"<="),("CROSSING PROBABILITY",policy.crossing_probability,"<="),
                ("CROSSINGS",policy.crossings,"<=")):
            gates.append(_gate(name,False,None,threshold,operator))
    reference_status=final_candidate.get("reference_risk","REFERENCE_UNKNOWN")
    gates.append(_gate("DEV_P95 ROBUST TIER",reference_status=="REFERENCE_ROBUST",reference_status,
        dev_p95_bps,"ROBUST",blocking=not policy.tiered_reference_risk))
    reference_pass=(reference_status in {"REFERENCE_ROBUST","REFERENCE_CAUTION"}
                    if policy.tiered_reference_risk else reference_status=="REFERENCE_ROBUST")
    gates.append(_gate("REFERENCE POLICY",reference_pass,reference_status,
        "ROBUST_OR_CAUTION" if policy.tiered_reference_risk else "ROBUST","IN"))
    quote_verified=bool(quote is not None and getattr(quote,"quote_verified",False) and quote_status=="READY")
    gates.append(_gate("VERIFIED QUOTE",quote_verified,quote_status,"READY","=="))
    gates.append(_gate("SIDE ASK",ask is not None and ask_size is not None,ask,None,"AVAILABLE"))
    fee_verified=bool(fee_metadata is not None and fee_metadata.verified)
    gates.append(_gate("VERIFIED FEE",fee_verified,getattr(fee_metadata,"fee_type",None),"VERIFIED","=="))
    fee=total_cost=break_even=point_edge=conservative_edge=point_ev=conservative_ev=None
    if ask is not None and fee_verified:
        try:
            assessed=KalshiFeeModel().assess(ask,fee_metadata)
            fee=assessed.total_fee; total_cost=assessed.total_cost
            confidence=Decimal(str(max(snapshot.p_yes,snapshot.p_no))); conservative=Decimal(str(snapshot.conservative_bound))
            break_even=(ask+fee)/ONE; point_edge=confidence-break_even; conservative_edge=conservative-break_even
            point_ev=confidence*ONE-ask-fee; conservative_ev=conservative*ONE-ask-fee
        except (ValueError,ArithmeticError): pass
    gates.extend((
        _gate("MAX TOTAL ENTRY COST",total_cost is not None and total_cost<MAX_TOTAL_ENTRY_COST,total_cost,MAX_TOTAL_ENTRY_COST,"<"),
        _gate("POINT NET EV",point_ev is not None and point_ev>0,point_ev,0,">"),
        _gate("CONSERVATIVE NET EV",conservative_ev is not None and conservative_ev>0,conservative_ev,0,">"),
        _gate("MIN CONSERVATIVE EDGE",conservative_edge is not None and conservative_edge>=policy.min_conservative_edge,
              conservative_edge,policy.min_conservative_edge,">=")))
    scan_key=f"{_iso(now)}|{window_end}"
    identifier=hashlib.sha256(f"{SCHEMA_VERSION}|{policy.version}|{asset}|{scan_key}".encode()).hexdigest()
    return {"schema_version":SCHEMA_VERSION,"sample_label":SAMPLE_LABEL,"observation_id":identifier,
        "scan_id":hashlib.sha256(scan_key.encode()).hexdigest(),"policy_version":policy.version,
        "timestamp_utc":_iso(now),"window_start_utc":window_start,"window_end_utc":window_end,
        "seconds_remaining":getattr(snapshot,"seconds_remaining",final_candidate.get("seconds_remaining")),
        "asset":asset,"predicted_side":side,
        "confidence":max(snapshot.p_yes,snapshot.p_no) if snapshot is not None else None,
        "conservative_probability":getattr(snapshot,"conservative_bound",None),
        "fragility":getattr(snapshot,"fragility",None),"disagreement":getattr(snapshot,"disagreement",None),
        "crossing_probability":getattr(snapshot,"crossing_probability",None),
        "crossings":getattr(snapshot,"reference_crossings",None),"dev_p95_reference_status":reference_status,
        "quote_status":quote_status,"quote_verified":quote_verified,"side_specific_ask":str(ask) if ask is not None else None,
        "ask_size":str(ask_size) if ask_size is not None else None,
        "quote_age_seconds":str(getattr(quote,"quote_age_seconds",None)) if quote is not None else None,
        "fee_type":getattr(fee_metadata,"fee_type",None),
        "fee_multiplier":str(getattr(fee_metadata,"fee_multiplier",None)) if fee_metadata is not None else None,
        "fee_schedule_provenance":getattr(fee_metadata,"fee_provenance",None),
        "fee_schedule_effective_date":getattr(fee_metadata,"fee_schedule_effective_date",None),
        "computed_fee":str(fee) if fee is not None else None,"total_entry_cost":str(total_cost) if total_cost is not None else None,
        "point_break_even":str(break_even) if break_even is not None else None,
        "conservative_break_even":str(break_even) if break_even is not None else None,
        "point_edge":str(point_edge) if point_edge is not None else None,
        "conservative_edge":str(conservative_edge) if conservative_edge is not None else None,
        "point_ev":str(point_ev) if point_ev is not None else None,
        "conservative_ev":str(conservative_ev) if conservative_ev is not None else None,
        "gates":gates,"final_candidate_qualification":bool(final_candidate.get("economically_valid")),
        "final_status":final_candidate.get("status"),"final_reason":final_candidate.get("reason"),
        "actionability":"SHADOW_COMPARISON_ONLY","authentication":"NONE","order_capability":"DISABLED"}


class PolicyTelemetryStore:
    """Append-only ledgers with bounded restart and diagnostic state."""
    def __init__(self, root: Path):
        self.root=Path(root).resolve(); self.root.mkdir(parents=True,exist_ok=True)
        self.observations=self.root/"policy_observations.jsonl"; self.windows=self.root/"policy_windows.jsonl"
        self._lock=threading.RLock(); self._observation_ids=deque(maxlen=RECENT_IDS); self._completion_ids=deque(maxlen=RECENT_IDS)
        self._completion_set=set(); self._counts=Counter(); self._blockers={"EXPERIMENTAL_MANUAL_SIGNAL_V2":Counter(),"EXPERIMENTAL_MANUAL_SIGNAL_V2_1":Counter(),
            "EXPERIMENTAL_MANUAL_SIGNAL_V2_2_ACTIVE":Counter()}
        self._first={}
        self._observed_windows={}
        for row in self._iter(self.observations):
            self._observation_ids.append(row.get("observation_id"))
            for gate in row.get("gates",[]):
                if not gate.get("passed") and gate.get("blocking",True): self._blockers.setdefault(row.get("policy_version"),Counter())[gate.get("gate")]+=1
        for row in self._tail(self.observations,RECENT_IDS):
            if row.get("window_end_utc"): self._observed_windows[row["window_end_utc"]]=row.get("window_start_utc")
            if row.get("policy_selected_this_scan"):
                key=(row.get("window_end_utc"),row.get("policy_version"))
                current=self._first.get(key)
                if current is None or row.get("timestamp_utc","")<current.get("timestamp_utc",""): self._first[key]=row
        for row in self._iter(self.windows):
            identifier=row.get("completion_id"); self._completion_ids.append(identifier)
            policy=row.get("policy_version"); self._counts["completed_windows"]+=1 if policy.endswith("V2") else 0
            self._counts[policy+":signal"]+=int(bool(row.get("signal_issued")))
            self._counts[policy+":no_signal"]+=int(not row.get("signal_issued"))
        self._completion_set=set(self._completion_ids)

    @staticmethod
    def _iter(path):
        if not path.exists():return
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try: row=json.loads(line)
                except json.JSONDecodeError: continue
                if isinstance(row,dict):yield row

    @staticmethod
    def _tail(path, limit):
        if not path.exists():return []
        rows=deque(maxlen=limit)
        for row in PolicyTelemetryStore._iter(path):rows.append(row)
        return list(rows)

    def _append(self,path,row):
        with path.open("a",encoding="utf-8",newline="\n") as handle:
            handle.write(json.dumps(row,sort_keys=True,separators=(",",":"))+"\n"); handle.flush(); os.fsync(handle.fileno())

    def append_scan(self, records: list[dict]):
        if len(records)!=len(ASSETS)*POLICIES_PER_WINDOW: raise ValueError("synchronized scan requires twelve policy records")
        scan_ids={r.get("scan_id") for r in records}; pairs={(r.get("asset"),r.get("policy_version")) for r in records}
        if len(scan_ids)!=1 or len(pairs)!=len(records): raise ValueError("policy scan alignment failure")
        with self._lock:
            for row in records:
                identifier=row["observation_id"]
                if identifier in self._observation_ids:continue
                self._append(self.observations,row); self._observation_ids.append(identifier)
                if row.get("window_end_utc"): self._observed_windows[row["window_end_utc"]]=row.get("window_start_utc")
                if row.get("policy_selected_this_scan"):
                    self._first.setdefault((row["window_end_utc"],row["policy_version"]),row)
                for gate in row["gates"]:
                    if not gate["passed"] and gate.get("blocking",True):self._blockers[row["policy_version"]][gate["gate"]]+=1

    def complete_before(self, current_window_start: str, policies):
        """Finalize every observed expired window, including explicit no-signal rows."""
        for window_end,window_start in sorted(self._observed_windows.items()):
            if window_end<=current_window_start:self.complete_window(window_start,window_end,policies)

    def complete_window(self, window_start: str, window_end: str, policies):
        with self._lock:
            for policy in policies:
                identifier=hashlib.sha256(f"{SCHEMA_VERSION}|COMPLETE|{policy.version}|{window_end}".encode()).hexdigest()
                if identifier in self._completion_set:continue
                first=self._first.get((window_end,policy.version)); selected=(first or {})
                row={"schema_version":SCHEMA_VERSION,"sample_label":SAMPLE_LABEL,"completion_id":identifier,
                    "policy_version":policy.version,"window_start_utc":window_start,"window_end_utc":window_end,
                    "completed_at_utc":datetime.now(UTC).isoformat(),"signal_issued":bool(first),
                    "selected_asset":selected.get("asset"),"selected_side":selected.get("predicted_side"),
                    "first_qualifying_timestamp":selected.get("timestamp_utc"),
                    "seconds_remaining_at_first_qualification":selected.get("seconds_remaining"),
                    "terminal_status":"SIGNAL" if first else "NO SIGNAL","explicit_no_signal":not bool(first),
                    "actionability":"SHADOW_COMPARISON_ONLY","authentication":"NONE","order_capability":"DISABLED"}
                self._append(self.windows,row); self._completion_ids.append(identifier); self._completion_set=set(self._completion_ids)
                self._counts["completed_windows"]+=1 if policy.version.endswith("V2") else 0
                self._counts[policy.version+":signal"]+=int(bool(first)); self._counts[policy.version+":no_signal"]+=int(not first)

    def summary(self, v2, v21, v22):
        completed=self._counts["completed_windows"]
        def stats(policy):
            yes=self._counts[policy.version+":signal"]; no=self._counts[policy.version+":no_signal"]
            top=self._blockers[policy.version].most_common(1)
            return {"signals":yes,"no_signals":no,"signal_rate":yes/(yes+no) if yes+no else None,
                    "top_blocker":top[0][0] if top else None,"top_blocker_count":top[0][1] if top else 0}
        return {"sample_label":SAMPLE_LABEL,"completed_windows":completed,"v2":stats(v2),"v21":stats(v21),"v22":stats(v22),
                "observation_buffer_cap":RECENT_IDS,"completion_dedupe_cap":RECENT_IDS}
