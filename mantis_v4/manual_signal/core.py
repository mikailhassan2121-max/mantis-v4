"""Step 10 experimental manual-signal policy.

No authenticated client, portfolio method, order method, sizing algorithm, or
execution capability is present.  The operator remains the sole decision maker.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from typing import Any

from mantis_v4.economics.kalshi import KALSHI_PROVIDER, SERIES_BY_ASSET
from mantis_v4.kalshi_forward.core import ASSETS, MODEL_POLICY, original_qualified
from mantis_v4.reference.kalshi_reference_risk import assess_reference_risk

POLICY_VERSION = "EXPERIMENTAL_MANUAL_SIGNAL_V1"
REFERENCE_POLICY = "KALSHI_REFERENCE_RISK_V1_SHADOW / DEV_P95"
ACTIONABILITY = "EXPERIMENTAL_MANUAL_SIGNAL_ONLY"
VALIDATION_STATUS = "EXPERIMENTAL_NOT_YET_FORWARD_VALIDATED"
FEE_MODEL_VERSION = "KALSHI_QUADRATIC_TAKER_2026_07_07_V1"
FEE_SCHEDULE_EFFECTIVE_DATE = "2026-07-07"
FEE_PROVENANCE = "KALSHI_OFFICIAL_FEE_SCHEDULE_2026_07_07_AND_PUBLIC_SERIES_METADATA"
ASSET_ORDER = {asset:index for index,asset in enumerate(ASSETS)}
CENT = Decimal("0.01")
CENTICENT = Decimal("0.0001")
ONE = Decimal("1.0000")


def initial_manual_selection() -> dict:
    """Authoritative state published before the first potentially slow scan."""
    rows=[{"asset":asset,"side":None,"confidence":None,"conservative_probability":None,
        "fragility":None,"disagreement":None,"crossing_probability":None,"crossings":None,
        "seconds_remaining":None,"status":"SCANNING","reason":"AWAITING FIRST KALSHI SCAN",
        "policy_version":POLICY_VERSION,"event_market_provider":KALSHI_PROVIDER,
        "manual_only":True,"production_validated":False,"authentication":"NONE",
        "order_capability":"DISABLED","economically_valid":False,
        "reference_risk":"REFERENCE_UNKNOWN"} for asset in ASSETS]
    operator={"mode":"KALSHI_MANUAL_SIGNAL","headline":"SCANNING",
        "reason":"AWAITING FIRST KALSHI SCAN","primary_selection":None,
        "strongest_candidate":None,"candidate_rankings":rows,
        "selection_policy_version":POLICY_VERSION,"policy":POLICY_VERSION,
        "actionability":ACTIONABILITY,"reference_policy":REFERENCE_POLICY,
        "validation_status":VALIDATION_STATUS,"manual_only":True,
        "production_validated":False,"manual_execution_only":True,
        "authentication":"NONE","order_capability":"DISABLED",
        "event_market_provider":KALSHI_PROVIDER,"seconds_until_entry_eligible":None,
        "current_window":None,"first_scan_complete":False}
    return {"selection_policy":POLICY_VERSION,"status":"SCANNING","selected":None,
        "strongest_candidate":None,"candidates":rows,"operator_state":operator}


@dataclass(frozen=True)
class FeeMetadata:
    series_ticker: str
    fee_type: str
    fee_multiplier: Decimal
    fee_schedule_effective_date: str
    fee_provenance: str
    verified_at_utc: datetime
    verified: bool
    scheduled_changes: tuple[dict, ...] = ()


@dataclass(frozen=True)
class FeeAssessment:
    price: Decimal
    contracts: Decimal
    trade_fee: Decimal
    rounding_fee: Decimal
    total_fee: Decimal
    total_cost: Decimal
    fee_model: str
    fee_metadata: FeeMetadata


class KalshiFeeModel:
    """Official July 7 2026 quadratic taker fee for a one-contract fill."""
    coefficient = Decimal("0.07")

    @staticmethod
    def _ceil(value: Decimal, quantum: Decimal) -> Decimal:
        return value.quantize(quantum, rounding=ROUND_CEILING)

    def assess(self, price: Decimal, metadata: FeeMetadata,
               contracts: Decimal = Decimal("1")) -> FeeAssessment:
        if not metadata.verified or metadata.fee_type != "quadratic":
            raise ValueError("verified quadratic series fee metadata required")
        if metadata.series_ticker not in set(SERIES_BY_ASSET.values()):
            raise ValueError("unsupported series fee metadata")
        if any(not value.is_finite() for value in (price,contracts,metadata.fee_multiplier)):
            raise ValueError("finite fee inputs required")
        if not Decimal("0") < price < ONE or contracts != ONE or metadata.fee_multiplier < 0:
            raise ValueError("one positive whole contract and 0<price<1 required")
        raw=metadata.fee_multiplier*self.coefficient*contracts*price*(ONE-price)
        trade_fee=self._ceil(raw,CENTICENT)
        position_cost=price*contracts
        debit=self._ceil(position_cost+trade_fee,CENT)
        rounding_fee=debit-position_cost-trade_fee
        if rounding_fee < 0: raise ValueError("invalid fee rounding")
        return FeeAssessment(price,contracts,trade_fee,rounding_fee,
            trade_fee+rounding_fee,debit,FEE_MODEL_VERSION,metadata)


class FeeMetadataVerifier:
    """Read-only public series metadata plus official fee-change verification."""
    def __init__(self, client, now=None):
        self.client=client; self.now=now or (lambda:datetime.now(UTC)); self._cache={}

    def verify(self, asset: str) -> FeeMetadata:
        if asset not in SERIES_BY_ASSET: raise ValueError("unsupported asset")
        ticker=SERIES_BY_ASSET[asset]
        if ticker in self._cache: return self._cache[ticker]
        observed=self.now().astimezone(UTC)
        try:
            series=self.client.get("/series/"+ticker).get("series")
            changes=self.client.get("/series/fee_changes",series_ticker=ticker,
                                    show_historical="true").get("series_fee_change_arr")
            if not isinstance(series,dict) or not isinstance(changes,list): raise ValueError("fee metadata malformed")
            fee_type=str(series.get("fee_type") or "")
            multiplier=Decimal(str(series.get("fee_multiplier")))
            relevant=[]
            for change in changes:
                if not isinstance(change,dict) or change.get("series_ticker") not in (None,ticker):
                    raise ValueError("fee change metadata mismatch")
                relevant.append(dict(change))
            verified=(series.get("ticker")==ticker and series.get("frequency")=="fifteen_min" and
                      fee_type=="quadratic" and multiplier==Decimal("1") and
                      all(str(c.get("fee_type") or fee_type)=="quadratic" for c in relevant))
            result=FeeMetadata(ticker,fee_type,multiplier,FEE_SCHEDULE_EFFECTIVE_DATE,
                FEE_PROVENANCE,observed,verified,tuple(relevant))
        except Exception:
            result=FeeMetadata(ticker,"UNVERIFIED",Decimal("NaN"),FEE_SCHEDULE_EFFECTIVE_DATE,
                FEE_PROVENANCE,observed,False,())
        self._cache[ticker]=result
        return result


def _decimal(value) -> Decimal:
    result=Decimal(str(value))
    if not result.is_finite(): raise ValueError("finite Decimal required")
    return result


def evaluate_candidate(*, snapshot, mapping, quote, quote_status: str,
                       dev_p95_bps: Decimal | None, fee_metadata: FeeMetadata,
                       now: datetime, fee_model: KalshiFeeModel | None = None) -> dict:
    """Evaluate one asset in a fixed fail-closed order without changing probability."""
    fee_model=fee_model or KalshiFeeModel(); asset=snapshot.asset
    confidence=Decimal(str(max(snapshot.p_yes,snapshot.p_no)))
    candidate={"asset":asset,"side":snapshot.predicted_side,"confidence":float(confidence),
        "conservative_probability":snapshot.conservative_bound,"fragility":snapshot.fragility,
        "disagreement":snapshot.disagreement,"crossing_probability":snapshot.crossing_probability,
        "crossings":snapshot.reference_crossings,"seconds_remaining":snapshot.seconds_remaining,
        "contract_id":f"{asset}|{mapping.window_start_utc.isoformat()}|{mapping.window_end_utc.isoformat()}|15m",
        "window_start_utc":mapping.window_start_utc.isoformat(),"window_end_utc":mapping.window_end_utc.isoformat(),
        "series_ticker":mapping.series_ticker,"event_ticker":mapping.event_ticker,"market_ticker":mapping.market_ticker,
        "target":str(mapping.target),"proxy_current":str(snapshot.current_price),
        "event_market_source":KALSHI_PROVIDER,"event_market_provider":KALSHI_PROVIDER,
        "quote_provenance":KALSHI_PROVIDER,"model_policy":MODEL_POLICY,"reference_policy":REFERENCE_POLICY,
        "policy_version":POLICY_VERSION,"validation_status":VALIDATION_STATUS,"manual_only":True,
        "production_validated":False,"authentication":"NONE","order_capability":"DISABLED",
        "status":"NO SIGNAL","reason":"UNASSESSED","economically_valid":False,"reference_risk":"REFERENCE_UNKNOWN",
        "fee_type":fee_metadata.fee_type,"fee_multiplier":str(fee_metadata.fee_multiplier),
        "fee_provenance":fee_metadata.fee_provenance,"fee_schedule_effective_date":fee_metadata.fee_schedule_effective_date,
        "fee_verified":fee_metadata.verified}
    if mapping.asset!=asset or not mapping.mapping_verified or mapping.window_end_utc<=now or mapping.status.lower()!="active":
        candidate.update(status="NO SIGNAL",reason="MARKET MAPPING INVALID"); return candidate
    if quote is not None:
        candidate.update(yes_bid=str(quote.yes_bid) if quote.yes_bid is not None else None,
            yes_ask=str(quote.yes_ask) if quote.yes_ask is not None else None,
            yes_ask_size=str(quote.yes_ask_size) if quote.yes_ask_size is not None else None,
            no_bid=str(quote.no_bid) if quote.no_bid is not None else None,
            no_ask=str(quote.no_ask) if quote.no_ask is not None else None,
            no_ask_size=str(quote.no_ask_size) if quote.no_ask_size is not None else None,
            quote_received_at_utc=quote.received_at_utc.isoformat(),
            quote_age_seconds=str(quote.quote_age_seconds),quote_verified=quote.quote_verified)
    if snapshot.seconds_remaining>300:
        candidate.update(status="WAIT T-300",reason="AWAITING T-300 ENTRY WINDOW"); return candidate
    if not original_qualified(snapshot):
        candidate.update(status="CONF FAIL",reason="FROZEN MODEL GATES NOT SATISFIED"); return candidate
    risk=assess_reference_risk(asset=asset,target=mapping.target,proxy_current=_decimal(snapshot.current_price),
                               uncertainty_bound_bps=dev_p95_bps)
    candidate.update(reference_risk=risk.classification,distance_bps=str(risk.distance_bps) if risk.distance_bps is not None else None,
                     reference_bound_bps=str(dev_p95_bps) if dev_p95_bps is not None else None)
    if risk.classification!="REFERENCE_ROBUST":
        candidate.update(status="REF AMBIGUOUS",reason="REFERENCE AMBIGUOUS UNDER DEV_P95"); return candidate
    if quote_status=="QUOTE_STALE": candidate.update(status="QUOTE STALE",reason="KALSHI QUOTE STALE"); return candidate
    if quote is None or not quote.quote_verified:
        candidate.update(status="QUOTE UNAVAILABLE",reason="VERIFIED KALSHI QUOTE UNAVAILABLE"); return candidate
    if quote.window_start_utc!=mapping.window_start_utc or quote.window_end_utc!=mapping.window_end_utc or not quote.fresh_at(now,5):
        candidate.update(status="QUOTE STALE",reason="KALSHI QUOTE STALE OR WRONG WINDOW"); return candidate
    ask=quote.yes_ask if snapshot.predicted_side=="YES" else quote.no_ask
    ask_size=quote.yes_ask_size if snapshot.predicted_side=="YES" else quote.no_ask_size
    if ask is None or ask_size is None:
        candidate.update(status="QUOTE UNAVAILABLE",reason="REQUIRED ORDERBOOK SIDE UNAVAILABLE"); return candidate
    candidate.update(ask=str(ask),ask_size=str(ask_size),needed_ask=str(ask),quote_verified=True)
    if not fee_metadata.verified:
        candidate.update(status="FEE UNVERIFIED",reason="KALSHI SERIES FEE UNVERIFIED"); return candidate
    try: fee=fee_model.assess(ask,fee_metadata)
    except ValueError:
        candidate.update(status="FEE UNVERIFIED",reason="KALSHI SERIES FEE UNVERIFIED"); return candidate
    payout=ONE; p=confidence; p_lcb=_decimal(snapshot.conservative_bound)
    gross_ev=p*payout-ask; net_ev=gross_ev-fee.total_fee
    conservative_net_ev=p_lcb*payout-ask-fee.total_fee
    net_break_even=(ask+fee.total_fee)/payout
    model_edge=p-net_break_even; conservative_edge=p_lcb-net_break_even
    candidate.update(fee=str(fee.total_fee),trade_fee=str(fee.trade_fee),rounding_fee=str(fee.rounding_fee),
        total_cost=str(fee.total_cost),fee_type=fee_metadata.fee_type,fee_multiplier=str(fee_metadata.fee_multiplier),
        fee_model=fee.fee_model,fee_provenance=fee_metadata.fee_provenance,
        fee_schedule_effective_date=fee_metadata.fee_schedule_effective_date,
        gross_ev=str(gross_ev),net_ev=str(net_ev),conservative_net_ev=str(conservative_net_ev),
        net_break_even=str(net_break_even),model_edge=str(model_edge),conservative_edge=str(conservative_edge))
    if ask+fee.total_fee>=payout:
        candidate.update(status="ECON FAIL",reason="ECONOMICALLY IMPOSSIBLE"); return candidate
    if net_ev<=0 or conservative_net_ev<=0:
        candidate.update(status="ECON FAIL",reason="POSITIVE POINT AND CONSERVATIVE EV REQUIRED"); return candidate
    candidate.update(status="ELIGIBLE",reason="ALL EXPERIMENTAL GATES PASSED",economically_valid=True)
    return candidate


def select_primary_signal(candidates: list[dict]) -> dict:
    fixed={row.get("asset"):dict(row) for row in candidates}
    if set(fixed)!=set(ASSETS): raise ValueError("exact four-asset candidate universe required")
    rows=[fixed[a] for a in ASSETS]; eligible=[r for r in rows if r.get("status")=="ELIGIBLE" and r.get("economically_valid")]
    def key(r):
        return (-float(r["conservative_edge"]),-float(r["model_edge"]),-float(r["conservative_probability"]),
            -float(r["confidence"]),float(r["fragility"]),float(r["disagreement"]),
            float(r["crossing_probability"]),int(r["crossings"]),ASSET_ORDER[r["asset"]])
    eligible.sort(key=key); selected=dict(eligible[0]) if eligible else None
    if selected:
        selected["status"]="PRIMARY"; fixed[selected["asset"]]=selected
        for row in eligible[1:]: fixed[row["asset"]].update(status="NO SIGNAL",reason="LOWER RANKED QUALIFYING CANDIDATE")
    ordered=[fixed[a] for a in ASSETS]
    strongest=selected or max(ordered,key=lambda r:(float(r.get("confidence") or 0),-ASSET_ORDER[r["asset"]]))
    reason="ALL EXPERIMENTAL GATES PASSED" if selected else strongest.get("reason","NO CANDIDATE")
    waiting=bool(ordered) and all(r.get("status") in {"WAIT T-300","MARKET INITIALIZING","SCANNING"} for r in ordered)
    headline="PRIMARY SIGNAL" if selected else ("SCANNING" if waiting else "NO SIGNAL")
    seconds_values=[float(r["seconds_remaining"]) for r in ordered if r.get("seconds_remaining") is not None]
    seconds_until=max(0.0,min(seconds_values)-300.0) if seconds_values else None
    operator={"mode":"KALSHI_MANUAL_SIGNAL","headline":headline,
        "reason":reason,"primary_selection":selected,"strongest_candidate":strongest,
        "candidate_rankings":ordered,"selection_policy_version":POLICY_VERSION,
        "policy":POLICY_VERSION,"actionability":ACTIONABILITY,"reference_policy":REFERENCE_POLICY,
        "validation_status":VALIDATION_STATUS,"manual_only":True,"production_validated":False,
        "manual_execution_only":True,"authentication":"NONE","order_capability":"DISABLED",
        "event_market_provider":KALSHI_PROVIDER,"seconds_until_entry_eligible":seconds_until,
        "current_window":strongest.get("contract_id"),"first_scan_complete":True}
    return {"selection_policy":POLICY_VERSION,"status":operator["headline"],"selected":selected,
        "strongest_candidate":strongest,"candidates":ordered,"operator_state":operator}


class ManualSignalStore:
    """Append-only signal-only ledger; never records an operator trade."""
    def __init__(self, root: Path):
        self.root=Path(root).resolve(); self.root.mkdir(parents=True,exist_ok=True)
        self.path=self.root/"signals.jsonl"; self._lock=threading.Lock(); self._ids=set()
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                try: self._ids.add(json.loads(line)["signal_id"])
                except (json.JSONDecodeError,KeyError): raise ValueError("manual signal ledger malformed")

    @staticmethod
    def signal_id(signal: dict) -> str:
        return hashlib.sha256(f"{POLICY_VERSION}|{signal['contract_id']}|{signal['side']}".encode()).hexdigest()

    def append(self, signal: dict, observed_at: datetime) -> bool:
        if signal.get("status")!="PRIMARY" or not signal.get("economically_valid"):
            raise ValueError("only valid experimental primary signals may be persisted")
        identifier=self.signal_id(signal)
        record={"schema_version":"KALSHI_MANUAL_SIGNAL_V1","signal_id":identifier,
            "policy_version":POLICY_VERSION,"reference_policy":REFERENCE_POLICY,"model_policy":MODEL_POLICY,
            "observed_at_utc":observed_at.astimezone(UTC).isoformat(),**signal,
            "manual_only":True,"production_validated":False,"operator_traded":None,
            "authentication":"NONE","order_capability":"DISABLED"}
        with self._lock:
            if identifier in self._ids:return False
            with self.path.open("a",encoding="utf-8",newline="\n") as handle:
                handle.write(json.dumps(record,sort_keys=True,separators=(",",":"))+"\n"); handle.flush(); os.fsync(handle.fileno())
            self._ids.add(identifier); return True
