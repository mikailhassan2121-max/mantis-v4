"""Strict executable-price economics for $1 binary contracts."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import math
from typing import Optional

UTC=timezone.utc

class FeeStatus(str,Enum):
    VERIFIED="VERIFIED_FEES"; USER_CONFIGURED="USER_CONFIGURED_FEES"; UNKNOWN="UNKNOWN_FEES"
class SlippageStatus(str,Enum):
    VERIFIED="VERIFIED_SLIPPAGE"; USER_CONFIGURED="USER_CONFIGURED_SLIPPAGE"; NOT_MODELED="SLIPPAGE_NOT_MODELED"
class EconomicStatus(str,Enum):
    ROBUST_POSITIVE_EV="ROBUST_POSITIVE_EV"; MARGINAL_EV="MARGINAL_EV"
    NEGATIVE_EV="NEGATIVE_EV"; NON_ROBUST_EV="NON_ROBUST_EV"
    UNAVAILABLE="ECONOMICS_UNAVAILABLE"; INVALID_STALE="INVALID_OR_STALE_QUOTE"
    UNVERIFIED="ECONOMICS_UNVERIFIED"

@dataclass(frozen=True)
class ContractEconomics:
    asset:str; contract_id:str; reference:float; resolution_time:datetime
    settlement_rule:str; yes_bid:float; yes_ask:float; no_bid:float; no_ask:float
    payout:float; quote_timestamp:datetime; quote_source:str
    reference_verified:bool; quote_verified:bool
    yes_depth:Optional[float]=None; no_depth:Optional[float]=None
    fee_per_contract:Optional[float]=None; fee_status:FeeStatus=FeeStatus.UNKNOWN
    slippage_per_contract:Optional[float]=None; slippage_status:SlippageStatus=SlippageStatus.NOT_MODELED

    def ask(self,side:str)->float: return self.yes_ask if side.upper()=="YES" else self.no_ask
    def bid(self,side:str)->float: return self.yes_bid if side.upper()=="YES" else self.no_bid
    def quote_age(self,now:datetime)->float:
        return (now.astimezone(UTC)-self.quote_timestamp.astimezone(UTC)).total_seconds()

@dataclass(frozen=True)
class BookDiagnostics:
    yes_bid_plus_no_bid:float; yes_ask_plus_no_ask:float
    yes_spread:float; no_spread:float; coherent:bool; anomalies:tuple[str,...]

@dataclass(frozen=True)
class EconomicAssessment:
    side:str; model_probability:float; lower_bound:float; ask:Optional[float]
    break_even_probability:Optional[float]; model_edge:Optional[float]; lcb_edge:Optional[float]
    gross_ev:Optional[float]; net_ev:Optional[float]; lcb_gross_ev:Optional[float]
    lcb_net_ev:Optional[float]; expected_return_on_cost:Optional[float]
    max_loss:Optional[float]; max_payout:Optional[float]; quote_age_seconds:Optional[float]
    status:EconomicStatus; ev_label:str; fees_status:str; slippage_status:str
    book:Optional[BookDiagnostics]; reasons:tuple[str,...]

def break_even_probability(price:float,payout:float=1.0,costs:float=0.0)->float:
    if not all(math.isfinite(x) for x in (price,payout,costs)) or payout<=0 or price<=0 or costs<0:
        raise ValueError("price/payout/costs are invalid")
    return (price+costs)/payout

def expected_value(probability:float,price:float,payout:float=1.0,costs:float=0.0)->float:
    if not 0<=probability<=1: raise ValueError("probability outside [0,1]")
    return probability*payout-price-costs

def maximum_purchase_price(probability:float,payout:float=1.0,edge:float=0.0,costs:float=0.0)->float:
    if not 0<=probability<=1 or not 0<=edge<=1: raise ValueError("probability/edge outside [0,1]")
    return max(0.0,probability*payout-edge*payout-costs)

def book_diagnostics(e:ContractEconomics,tolerance:float=.02)->BookDiagnostics:
    anomalies=[]
    if e.yes_bid>e.yes_ask: anomalies.append("YES_BID_ABOVE_ASK")
    if e.no_bid>e.no_ask: anomalies.append("NO_BID_ABOVE_ASK")
    bid_sum=e.yes_bid+e.no_bid; ask_sum=e.yes_ask+e.no_ask
    if bid_sum>e.payout+tolerance: anomalies.append("CROSS_SIDE_BIDS_EXCEED_PAYOUT")
    if ask_sum<e.payout-tolerance: anomalies.append("CROSS_SIDE_ASKS_BELOW_PAYOUT")
    return BookDiagnostics(bid_sum,ask_sum,e.yes_ask-e.yes_bid,e.no_ask-e.no_bid,
                           not anomalies,tuple(anomalies))

def _validate(e:ContractEconomics,now:datetime,max_age:float,expected_id:str,expected_reference:float)->tuple[str,...]:
    errors=[]; nums=(e.reference,e.yes_bid,e.yes_ask,e.no_bid,e.no_ask,e.payout)
    if not all(math.isfinite(x) for x in nums): errors.append("NONFINITE_ECONOMICS")
    if e.payout<=0: errors.append("INVALID_PAYOUT")
    for side,bid,ask in (("YES",e.yes_bid,e.yes_ask),("NO",e.no_bid,e.no_ask)):
        if ask<=0 or ask>e.payout: errors.append(f"INVALID_{side}_ASK")
        if bid<0 or bid>ask: errors.append(f"INVALID_{side}_BOOK")
    if e.quote_timestamp.tzinfo is None: errors.append("QUOTE_TIMESTAMP_NAIVE")
    elif e.quote_age(now)<0 or e.quote_age(now)>max_age: errors.append("QUOTE_STALE")
    if e.resolution_time.tzinfo is None or e.resolution_time<=now: errors.append("CONTRACT_EXPIRED")
    if e.contract_id!=expected_id: errors.append("CONTRACT_ID_MISMATCH")
    if not e.reference_verified or not e.quote_verified: errors.append("SOURCE_UNVERIFIED")
    if not math.isclose(e.reference,expected_reference,rel_tol=0,abs_tol=max(1e-9,abs(expected_reference)*1e-9)):
        errors.append("REFERENCE_MISMATCH")
    errors.extend(book_diagnostics(e).anomalies)
    return tuple(errors)

def assess_economics(e:Optional[ContractEconomics],*,side:str,model_probability:float,
 lower_bound:float,now:datetime,max_quote_age:float,expected_contract_id:str,
 expected_reference:float,marginal_edge:float=.02)->EconomicAssessment:
    if e is None:
        return EconomicAssessment(side,model_probability,lower_bound,None,None,None,None,None,None,None,None,None,None,None,None,EconomicStatus.UNAVAILABLE,"UNAVAILABLE",FeeStatus.UNKNOWN.value,SlippageStatus.NOT_MODELED.value,None,("contract economics unavailable",))
    errors=_validate(e,now,max_quote_age,expected_contract_id,expected_reference); book=book_diagnostics(e)
    if errors:
        status=EconomicStatus.UNVERIFIED if errors==("SOURCE_UNVERIFIED",) else EconomicStatus.INVALID_STALE
        return EconomicAssessment(side,model_probability,lower_bound,e.ask(side),None,None,None,None,None,None,None,None,None,e.payout,e.quote_age(now),status,"UNAVAILABLE",e.fee_status.value,e.slippage_status.value,book,errors)
    ask=e.ask(side); gross=expected_value(model_probability,ask,e.payout); lcbgross=expected_value(lower_bound,ask,e.payout)
    known_fees=e.fee_status is not FeeStatus.UNKNOWN
    slip_known=e.slippage_status is not SlippageStatus.NOT_MODELED
    costs=(e.fee_per_contract or 0)+(e.slippage_per_contract or 0)
    net=expected_value(model_probability,ask,e.payout,costs) if known_fees and slip_known else None
    lcbnet=expected_value(lower_bound,ask,e.payout,costs) if known_fees and slip_known else None
    be=break_even_probability(ask,e.payout,costs if known_fees and slip_known else 0)
    point_for_status=net if net is not None else gross; lcb_for_status=lcbnet if lcbnet is not None else lcbgross
    edge=model_probability-be; lcb_edge=lower_bound-be
    status=(EconomicStatus.NEGATIVE_EV if point_for_status<=0 else EconomicStatus.NON_ROBUST_EV if lcb_for_status<=0
            else EconomicStatus.MARGINAL_EV if lcb_edge<marginal_edge else EconomicStatus.ROBUST_POSITIVE_EV)
    label="NET_EV" if net is not None else "EV_BEFORE_UNVERIFIED_FEES"
    acquisition=ask+costs if net is not None else ask
    return EconomicAssessment(side,model_probability,lower_bound,ask,be,edge,lcb_edge,gross,net,lcbgross,lcbnet,
      point_for_status/acquisition,acquisition,e.payout,e.quote_age(now),status,label,e.fee_status.value,e.slippage_status.value,book,())
