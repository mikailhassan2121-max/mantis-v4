from __future__ import annotations
from dataclasses import asdict,dataclass,field
from datetime import datetime
from enum import Enum
from typing import Any,Optional
class ReferenceStatus(str,Enum): OFFICIAL="OFFICIAL_VERIFIED_REFERENCE"; PROXY="PROXY_UNVERIFIED"
class UserAction(str,Enum): NONE="NONE"; BOUGHT_YES="BOUGHT_YES"; BOUGHT_NO="BOUGHT_NO"; SKIPPED="SKIPPED"
@dataclass(frozen=True)
class LiveAssetState:
 asset:str; contract_id:str; window_start:datetime; window_end:datetime; scan_timestamp:datetime
 reference:float; reference_status:ReferenceStatus; current_price:float; volatility_estimate:float
 p_yes:float; conservative_bound:float; fragility:float; disagreement:float
 crossing_probability:float; reference_crossings:int; data_age_seconds:float=0.0
 fetch_latency_seconds:float=0.0; volatility_regime:str="UNKNOWN"; provider_mode:str="proxy"
 economics:Any=None; quality_ok:bool=True; quality_reason:str="OK"
 @property
 def seconds_remaining(self): return max(0.,(self.window_end-self.scan_timestamp).total_seconds())
 @property
 def predicted_side(self): return "YES" if self.p_yes>=.5 else "NO"
 @property
 def buffer(self): return self.current_price-self.reference
@dataclass(frozen=True)
class LiveSnapshot:
 run_id:str; observation_id:str; timestamp_utc:str; timestamp_local:str; asset:str
 contract_id:str; window_start:str; window_end:str; seconds_remaining:float
 reference:float; reference_status:str; current_price:float; buffer:float
 volatility_estimate:float; p_yes:float; p_no:float; predicted_side:str
 conservative_bound:float; fragility:float; disagreement:float; crossing_probability:float
 reference_crossings:int; phase6_state:str; phase6_reason:str; final_decision:str; final_reason:str
 data_age_seconds:float; fetch_latency_seconds:float; next_scan_utc:str
 yes_bid:Optional[float]=None; yes_ask:Optional[float]=None; no_bid:Optional[float]=None; no_ask:Optional[float]=None
 quote_timestamp:Optional[str]=None; quote_age:Optional[float]=None; quote_status:str="UNAVAILABLE"
 break_even:Optional[float]=None; model_edge:Optional[float]=None; lcb_edge:Optional[float]=None
 point_ev:Optional[float]=None; lcb_ev:Optional[float]=None; expected_return_on_cost:Optional[float]=None
 fees_status:str="UNKNOWN_FEES"; slippage_status:str="SLIPPAGE_NOT_MODELED"; ev_status:str="ECONOMICS_UNAVAILABLE"
 outcome_status:str="PENDING"; user_action:str="NONE"; metadata:dict=field(default_factory=dict)
 def as_dict(self): return asdict(self)
