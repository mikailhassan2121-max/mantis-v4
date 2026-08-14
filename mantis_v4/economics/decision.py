"""Phase 6 classification precondition followed by Phase 7 EV gates."""
from dataclasses import dataclass,replace
from .models import ContractEconomics,EconomicStatus,assess_economics
from ..entry.decision import Decision,DecisionInputs,DecisionPolicy,DecisionResult,decide

@dataclass(frozen=True)
class EconomicDecisionPolicy:
    classification:DecisionPolicy
    maximum_quote_age:float=15.0
    minimum_model_edge:float=.05
    minimum_lcb_edge:float=.02
    minimum_expected_return_on_cost:float=0.0
    allow_classification_advisory:bool=False

def decide_with_economics(inputs:DecisionInputs,policy:EconomicDecisionPolicy,*,economics:ContractEconomics|None,now,reference:float)->DecisionResult:
    classified=decide(inputs,policy.classification)
    if classified.decision not in (Decision.ENTER_YES,Decision.ENTER_NO): return classified
    a=assess_economics(economics,side=classified.side,model_probability=inputs.probability.confidence,
      lower_bound=inputs.probability.conservative_bound,now=now,max_quote_age=policy.maximum_quote_age,
      expected_contract_id=inputs.contract_id,expected_reference=reference)
    meta={"economics":a}
    if a.status is EconomicStatus.UNAVAILABLE:
        return replace(classified,decision=Decision.WAIT,reason_code="CONTRACT_QUOTE_UNAVAILABLE",reasons=("contract economics unavailable",),ev_status=a.status.value,metadata=meta)
    if a.status is EconomicStatus.INVALID_STALE:
        return replace(classified,decision=Decision.DATA_HOLD,reason_code="INVALID_OR_STALE_QUOTE",reasons=a.reasons,ev_status=a.status.value,metadata=meta)
    if a.status is EconomicStatus.UNVERIFIED:
        return replace(classified,decision=Decision.NO_TRADE,reason_code="ECONOMICS_UNVERIFIED",reasons=a.reasons,ev_status=a.status.value,metadata=meta)
    point=a.net_ev if a.net_ev is not None else a.gross_ev; lcb=a.lcb_net_ev if a.lcb_net_ev is not None else a.lcb_gross_ev
    if point is None or point<=0: return replace(classified,decision=Decision.NO_TRADE,reason_code="NEGATIVE_EV",reasons=("expected value is not positive",),ev_status=EconomicStatus.NEGATIVE_EV.value,metadata=meta)
    if lcb is None or lcb<=0: return replace(classified,decision=Decision.WAIT,reason_code="NON_ROBUST_EV",reasons=("lower-bound expected value is not positive",),ev_status=EconomicStatus.NON_ROBUST_EV.value,metadata=meta)
    if a.model_edge<policy.minimum_model_edge or a.lcb_edge<policy.minimum_lcb_edge:
        return replace(classified,decision=Decision.WAIT,reason_code="EDGE_TOO_SMALL",reasons=("economic margin of safety is below configured threshold",),ev_status=EconomicStatus.MARGINAL_EV.value,metadata=meta)
    if a.expected_return_on_cost<policy.minimum_expected_return_on_cost:
        return replace(classified,decision=Decision.WAIT,reason_code="EXPECTED_RETURN_TOO_LOW",reasons=("expected return on cost is below configured threshold",),ev_status=EconomicStatus.MARGINAL_EV.value,metadata=meta)
    return replace(classified,reason_code="ROBUST_POSITIVE_EV",reasons=classified.reasons+(f"model edge {a.model_edge:.1%}",f"LCB edge {a.lcb_edge:.1%}"),ev_status=EconomicStatus.ROBUST_POSITIVE_EV.value,metadata=meta)
