"""Explicit Phase 7 gates. Economic thresholds are architectural, unvalidated."""
from dataclasses import dataclass
from .decision import EconomicDecisionPolicy
from ..entry.decision import DecisionPolicy

@dataclass(frozen=True)
class Phase7Config:
    minimum_model_probability:float=.95
    minimum_lower_bound:float=.90
    maximum_fragility:float=50.0
    maximum_disagreement:float=.05
    maximum_crossing_probability:float=.35
    maximum_reference_crossings:int=4
    maximum_quote_age:float=15.0
    minimum_model_edge:float=.05
    minimum_lcb_edge:float=.02
    minimum_expected_return_on_cost:float=0.0
    fees_mode:str="UNKNOWN_FEES"
    slippage_mode:str="SLIPPAGE_NOT_MODELED"
    provider_preference:tuple[str,...]=("webull-official","verified-manual","underlying-proxy")
    def validate(self):
        probs=(self.minimum_model_probability,self.minimum_lower_bound,self.maximum_disagreement,self.maximum_crossing_probability,self.minimum_model_edge,self.minimum_lcb_edge)
        if any(not 0<=x<=1 for x in probs) or not 0<=self.maximum_fragility<=100 or self.maximum_quote_age<=0 or self.maximum_reference_crossings<0: raise ValueError("invalid Phase 7 configuration")
        return self
    def policy(self)->EconomicDecisionPolicy:
        self.validate()
        classification=DecisionPolicy("H_p0.95_l0.90_f50_d.05_t300",probability_threshold=self.minimum_model_probability,lcb_threshold=self.minimum_lower_bound,max_fragility=self.maximum_fragility,max_disagreement=self.maximum_disagreement,max_crossing_probability=self.maximum_crossing_probability,max_seconds_remaining=300,max_crossings=self.maximum_reference_crossings)
        return EconomicDecisionPolicy(classification,self.maximum_quote_age,self.minimum_model_edge,self.minimum_lcb_edge,self.minimum_expected_return_on_cost)
