"""Post-qualification, cross-asset operator selection. No model mathematics."""
from __future__ import annotations
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any, Iterable

from .economics.fees import WEBULL_EVENT_FEE_PROVENANCE, WEBULL_EVENT_OPENING_FEE

LIVE_ASSETS = ("BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD")
SELECTION_POLICY_ID = "PRIMARY_SELECTOR_V1"
PAYOUT = Decimal("1.00")

def _d(value: Any) -> Decimal | None:
    if value is None: return None
    try: return Decimal(str(value))
    except Exception: return None

@dataclass(frozen=True)
class Candidate:
    asset: str; side: str | None; confidence: float | None; conservative_probability: float | None
    ask: float | None; net_break_even: float | None; model_edge: float | None
    conservative_edge: float | None; net_ev: float | None; conservative_net_ev: float | None
    fragility: float | None; disagreement: float | None; crossing_probability: float | None
    reference_crossings: int | None; status: str; reason: str; contract_id: str | None
    quote_provenance: str | None; fee: float = float(WEBULL_EVENT_OPENING_FEE)
    fee_provenance: str = WEBULL_EVENT_FEE_PROVENANCE

    def payload(self):
        value=asdict(self)
        provenance=str(self.quote_provenance or "").lower()
        value["quote_verified"]=(provenance in {"webull-official","verified-manual"} and self.ask is not None)
        value["economically_valid"]=(self.status=="ACTIONABLE" and value["quote_verified"] and
          self.net_ev is not None and self.net_ev>0 and self.conservative_net_ev is not None and self.conservative_net_ev>0)
        return value

def evaluate(snapshot: Any) -> Candidate:
    asset=str(getattr(snapshot,"asset","UNKNOWN")); side=getattr(snapshot,"predicted_side",None)
    confidence=max(getattr(snapshot,"p_yes",.5),getattr(snapshot,"p_no",.5)) if getattr(snapshot,"p_yes",None) is not None else None
    base=dict(asset=asset,side=side,confidence=confidence,
      conservative_probability=getattr(snapshot,"conservative_bound",None),fragility=getattr(snapshot,"fragility",None),
      disagreement=getattr(snapshot,"disagreement",None),crossing_probability=getattr(snapshot,"crossing_probability",None),
      reference_crossings=getattr(snapshot,"reference_crossings",None),contract_id=getattr(snapshot,"contract_id",None),
      quote_provenance=(getattr(snapshot,"metadata",None) or {}).get("provider_mode"))
    if getattr(snapshot,"phase6_state",None) not in ("ENTER YES","ENTER NO"):
        reason=getattr(snapshot,"phase6_reason",None) or "CONFIDENCE_GATE"
        if float(getattr(snapshot,"seconds_remaining",0) or 0)>300:
            reason="AWAITING_T300_ENTRY_WINDOW"
        return Candidate(**base,ask=None,net_break_even=None,model_edge=None,conservative_edge=None,
          net_ev=None,conservative_net_ev=None,status="NOT QUALIFIED",reason=reason)
    ask=_d(getattr(snapshot,"yes_ask",None) if side=="YES" else getattr(snapshot,"no_ask",None))
    p=_d(confidence); lcb=_d(getattr(snapshot,"conservative_bound",None))
    if ask is None:
        return Candidate(**base,ask=None,net_break_even=None,model_edge=None,conservative_edge=None,
          net_ev=None,conservative_net_ev=None,status="ECONOMICS UNVERIFIED",reason="QUOTE_UNAVAILABLE")
    fee=WEBULL_EVENT_OPENING_FEE
    if ask+fee>=PAYOUT:
        return Candidate(**base,ask=float(ask),net_break_even=float((ask+fee)/PAYOUT),model_edge=None,
          conservative_edge=None,net_ev=None,conservative_net_ev=None,status="EV FAIL",reason="ECONOMICALLY_IMPOSSIBLE")
    net=p*PAYOUT-ask-fee; conservative=lcb*PAYOUT-ask-fee; be=(ask+fee)/PAYOUT
    edge=p-be; conservative_edge=lcb-be
    if net<=0:
        status,reason="EV FAIL","NEGATIVE_NET_EV"
    elif conservative<=0:
        status,reason="EV FAIL","CONSERVATIVE_EV_LE_ZERO"
    elif getattr(snapshot,"final_decision",None) not in ("ENTER YES","ENTER NO"):
        status,reason="ECONOMICS UNVERIFIED",getattr(snapshot,"final_reason",None) or "ECONOMIC_GATE"
    else:
        status,reason="ACTIONABLE","ROBUST_POSITIVE_EV"
    return Candidate(**base,ask=float(ask),net_break_even=float(be),model_edge=float(edge),
      conservative_edge=float(conservative_edge),net_ev=float(net),conservative_net_ev=float(conservative),
      status=status,reason=reason)

def _rank(candidate: Candidate):
    return (candidate.conservative_edge or -99, candidate.model_edge or -99,
      candidate.conservative_probability or -99, candidate.confidence or -99,
      -(candidate.fragility or 999), -(candidate.disagreement or 999),
      -(candidate.crossing_probability or 999), -(candidate.reference_crossings or 999),
      -LIVE_ASSETS.index(candidate.asset))

def select_primary(snapshots: Iterable[Any]) -> dict:
    snapshots=list(snapshots); by_asset={getattr(s,"asset",None):s for s in snapshots}
    candidates=[evaluate(by_asset[a]) if a in by_asset else Candidate(a,None,None,None,None,None,None,None,None,None,None,None,None,None,"NO DATA","AWAITING_FIRST_SCAN",None,None) for a in LIVE_ASSETS]
    actionable=sorted((c for c in candidates if c.status=="ACTIONABLE"),key=_rank,reverse=True)
    chosen=actionable[0] if actionable else None
    strongest=sorted((c for c in candidates if c.confidence is not None),key=lambda c:((c.confidence or 0),-LIVE_ASSETS.index(c.asset)),reverse=True)
    remaining=max([float(getattr(s,"seconds_remaining",0) or 0) for s in snapshots],default=0)
    result={"selection_policy":SELECTION_POLICY_ID,"status":"PRIMARY SELECTION" if chosen else "NO TRADE",
      "earliest_entry_in_seconds":max(0,remaining-300),
      "selected":chosen.payload() if chosen else None,"strongest_candidate":strongest[0].payload() if strongest else None,
      "candidates":[c.payload() for c in candidates]}
    result["operator_state"]=operator_state(result, snapshots)
    return result

def operator_state(selection: dict, snapshots: Iterable[Any] = (), *, persisted: bool = False) -> dict:
    """Build the presentation contract; no frontend decision logic is needed."""
    snapshots=list(snapshots)
    selected=selection.get("selected")
    strongest=selection.get("strongest_candidate")
    candidates=list(selection.get("candidates") or [])
    by_asset={c.get("asset"):c for c in candidates}
    for snapshot in snapshots:
        asset=getattr(snapshot,"asset",None)
        if asset in LIVE_ASSETS and asset not in by_asset:
            by_asset[asset]=evaluate(snapshot).payload()
    candidates=[by_asset.get(a) or Candidate(a,None,None,None,None,None,None,None,None,None,None,None,None,None,
      "NO DATA","AWAITING_FIRST_SCAN",None,None).payload() for a in LIVE_ASSETS]
    has_scan=any(getattr(s,"timestamp_utc",None) for s in snapshots) or any(c.get("confidence") is not None for c in candidates)
    provider_bad=has_scan and all(c.get("status")=="NO DATA" for c in candidates)
    eligible=float(selection.get("earliest_entry_in_seconds") or 0)
    if selected:
        mode,headline,reason="PRIMARY_SELECTION","PRIMARY SELECTION","ECONOMICALLY VALIDATED"
    elif not has_scan:
        mode,headline,reason="STANDBY","STANDBY","AWAITING FIRST SCANNER SNAPSHOT"
    elif provider_bad:
        mode,headline,reason="PROVIDER_DEGRADED","DATA HOLD","UNDERLYING DATA UNAVAILABLE"
    elif eligible>0:
        mode,headline,reason="SCANNING","SCANNING","AWAITING T-300 ENTRY WINDOW"
    else:
        mode,headline="NO_TRADE","NO TRADE"
        reason=(strongest or {}).get("reason") or "NO ACTIONABLE CANDIDATE"
    window=next((getattr(s,"contract_id",None) for s in snapshots if getattr(s,"contract_id",None)),None)
    window_end=next((getattr(s,"window_end",None) for s in snapshots if getattr(s,"window_end",None)),None)
    return {"mode":mode,"headline":headline,"reason":reason,
      "primary_selection":selected,"strongest_candidate":strongest,
      "candidate_rankings":candidates,"economics_status":"VERIFIED" if selected else "UNVERIFIED",
      "selection_policy_version":SELECTION_POLICY_ID,"seconds_until_entry_eligible":eligible,
      "current_window":window,"window_end_utc":window_end,"provider_status":"DEGRADED" if provider_bad else "ACTIVE",
      "persisted":bool(persisted)}
