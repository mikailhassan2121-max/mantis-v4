from __future__ import annotations
from dataclasses import asdict,dataclass
from datetime import datetime,timedelta,timezone
import hashlib,json,subprocess,uuid
from pathlib import Path
from zoneinfo import ZoneInfo
from ..economics import Phase7Config,decide_with_economics
from ..entry import Decision,DecisionInputs,ProbabilityState,RiskDiagnostics,decide
from .events import AppEvent,EventBus,EventType
from .models import LiveAssetState,LiveSnapshot,UserAction
from .store import ForwardStore
UTC=timezone.utc
@dataclass(frozen=True)
class ForwardConfig:
 refresh_seconds:float=5.; maximum_data_age:float=90.; local_timezone:str="America/New_York"
 observation_only:bool=True; software_version:str="MANTIS_V4_PHASE8"; model_version:str="NORMAL_Z_PHASE6_LOCKED"
 minimum_report_entries:int=30
 def validate(self):
  if self.refresh_seconds<=0 or self.maximum_data_age<=0: raise ValueError("invalid forward timing configuration")
  ZoneInfo(self.local_timezone); return self
class ForwardEngine:
 def __init__(self,store:ForwardStore,config:ForwardConfig=ForwardConfig(),phase7:Phase7Config=Phase7Config(),events:EventBus|None=None,run_id:str|None=None,repo:Path|None=None,run_metadata:dict|None=None):
  self.store=store; self.config=config.validate(); self.phase7=phase7; self.policy=phase7.policy(); self.events=events or EventBus(); self.run_id=run_id or str(uuid.uuid4()); self.repo=repo or Path(__file__).resolve().parents[2]
  self.run_metadata=dict(run_metadata or {})
  self._last_window=None; self._write_run()
 def _git(self):
  try:return subprocess.check_output(["git","rev-parse","HEAD"],cwd=self.repo,text=True,stderr=subprocess.DEVNULL).strip()
  except Exception:return "UNKNOWN"
 def _write_run(self):
  cfg={"forward":asdict(self.config),"phase7":asdict(self.phase7)}; canonical=json.dumps(cfg,sort_keys=True,default=str)
  self.config_hash=hashlib.sha256(canonical.encode()).hexdigest(); self.git_commit=self._git()
  from .phase11 import POLICY_IDENTIFIER
  from .. import __version__
  row=dict(self.run_metadata)
  row.update({"run_id":self.run_id,"timestamp_utc":datetime.now(UTC).isoformat(),"software_version":self.config.software_version,"application_version":__version__,"git_commit":self.git_commit,"config_hash":self.config_hash,"model_version":self.config.model_version,"model_hash":hashlib.sha256(self.config.model_version.encode()).hexdigest(),"provider_modes":list(self.phase7.provider_preference),"policy_identifier":POLICY_IDENTIFIER,"observation_only":True,"demo":False})
  self.store.append("runs",row,"run_id")
 def process(self,state:LiveAssetState,user_action:UserAction=UserAction.NONE)->LiveSnapshot:
  now=state.scan_timestamp.astimezone(UTC); local=now.astimezone(ZoneInfo(self.config.local_timezone))
  window_key=(state.window_start,state.window_end)
  if self._last_window is not None and self._last_window!=window_key:self.events.emit(AppEvent(EventType.ROLLOVER,now.isoformat(),{"from":str(self._last_window),"to":str(window_key)}))
  self._last_window=window_key
  quality=state.quality_ok and state.data_age_seconds<=self.config.maximum_data_age
  di=DecisionInputs(state.asset,state.contract_id,state.seconds_remaining,ProbabilityState(state.p_yes,state.conservative_bound),RiskDiagnostics(0 if state.volatility_estimate<=0 else state.buffer/state.reference/state.volatility_estimate,state.fragility,state.disagreement,state.crossing_probability,state.volatility_regime,state.reference_crossings),data_fresh=quality,reference_valid=state.reference>0,contract_valid=bool(state.contract_id),sufficient_history=state.volatility_estimate>0)
  classified=decide(di,self.policy.classification)
  final=decide_with_economics(di,self.policy,economics=state.economics,now=now,reference=state.reference)
  assessment=final.metadata.get("economics") if final.metadata else None; e=state.economics
  oid=str(uuid.uuid4()); next_scan=now+timedelta(seconds=self.config.refresh_seconds)
  snap=LiveSnapshot(self.run_id,oid,now.isoformat(),local.isoformat(),state.asset,state.contract_id,state.window_start.astimezone(UTC).isoformat(),state.window_end.astimezone(UTC).isoformat(),state.seconds_remaining,state.reference,state.reference_status.value,state.current_price,state.buffer,state.volatility_estimate,state.p_yes,1-state.p_yes,state.predicted_side,state.conservative_bound,state.fragility,state.disagreement,state.crossing_probability,state.reference_crossings,classified.decision.value,classified.reason_code,final.decision.value,final.reason_code,state.data_age_seconds,state.fetch_latency_seconds,next_scan.isoformat(),
   yes_bid=getattr(e,"yes_bid",None),yes_ask=getattr(e,"yes_ask",None),no_bid=getattr(e,"no_bid",None),no_ask=getattr(e,"no_ask",None),quote_timestamp=getattr(e,"quote_timestamp",None).isoformat() if getattr(e,"quote_timestamp",None) else None,quote_age=getattr(assessment,"quote_age_seconds",None),quote_status=getattr(assessment,"status",None).value if assessment else "UNAVAILABLE",break_even=getattr(assessment,"break_even_probability",None),model_edge=getattr(assessment,"model_edge",None),lcb_edge=getattr(assessment,"lcb_edge",None),point_ev=(getattr(assessment,"net_ev",None) if getattr(assessment,"net_ev",None) is not None else getattr(assessment,"gross_ev",None)),lcb_ev=(getattr(assessment,"lcb_net_ev",None) if getattr(assessment,"lcb_net_ev",None) is not None else getattr(assessment,"lcb_gross_ev",None)),expected_return_on_cost=getattr(assessment,"expected_return_on_cost",None),fees_status=getattr(assessment,"fees_status","UNKNOWN_FEES"),slippage_status=getattr(assessment,"slippage_status","SLIPPAGE_NOT_MODELED"),ev_status=final.ev_status,user_action=user_action.value,metadata={"git_commit":self.git_commit,"config_hash":self.config_hash,"model_version":self.config.model_version,"provider_mode":state.provider_mode,"quality_reason":state.quality_reason,"volatility_regime":state.volatility_regime})
  self.store.append("observations",snap.as_dict(),"observation_id")
  if classified.decision in (Decision.ENTER_YES,Decision.ENTER_NO) and not self.store.has_entry(state.contract_id,state.asset):
   from .phase11 import POLICY_IDENTIFIER
   entry={"entry_id":str(uuid.uuid4()),"observation_id":oid,"run_id":self.run_id,"asset":state.asset,"contract_id":state.contract_id,"entry_timestamp":now.isoformat(),"seconds_remaining":state.seconds_remaining,"side":classified.side,"model_probability":max(state.p_yes,1-state.p_yes),"lower_bound":state.conservative_bound,"fragility":state.fragility,"disagreement":state.disagreement,"crossing_probability":state.crossing_probability,"reference_crossings":state.reference_crossings,"volatility_regime":state.volatility_regime,"reference":state.reference,"reference_status":state.reference_status.value,"entry_underlying_price":state.current_price,"provider_health":state.quality_reason,"provider_mode":state.provider_mode,"policy_identifier":POLICY_IDENTIFIER,"yes_ask":getattr(e,"yes_ask",None),"no_ask":getattr(e,"no_ask",None),"executable_ask":getattr(assessment,"ask",None),"quote_timestamp":snap.quote_timestamp,"quote_age":snap.quote_age,"break_even":getattr(assessment,"break_even_probability",None),"model_edge":getattr(assessment,"model_edge",None),"lcb_edge":getattr(assessment,"lcb_edge",None),"point_ev":snap.point_ev,"lcb_ev":snap.lcb_ev,"fees_status":snap.fees_status,"slippage_status":snap.slippage_status,"final_advisory":final.decision.value,"user_action":user_action.value,"git_commit":self.git_commit,"config_hash":self.config_hash}
   entry["fee_provenance"]=getattr(e,"fee_provenance",None)
   entry["quote_provenance"]=getattr(e,"quote_source",None)
   self.store.append("entries",entry,"entry_id"); self.events.emit(AppEvent(EventType.ENTRY_YES if classified.side=="YES" else EventType.ENTRY_NO,now.isoformat(),entry))
  else:self.events.emit(AppEvent(EventType.DATA_HOLD if final.decision is Decision.DATA_HOLD else EventType.WAIT,now.isoformat(),{"asset":state.asset,"reason":final.reason_code}))
  return snap
 def resolve(self,*,asset,contract_id,resolution_timestamp,terminal_value,reference,resolution_source="PROXY_UNDERLYING",verified=False):
  if self.store.has_resolution(contract_id,asset):
   return next(x for x in self.store.read("resolutions") if x.get("asset")==asset and x.get("contract_id")==contract_id)
  matches=[x for x in self.store.read("entries") if x.get("asset")==asset and x.get("contract_id")==contract_id]; side=matches[0]["side"] if matches else None; winning="YES" if terminal_value>=reference else "NO"
  record={"resolution_id":str(uuid.uuid4()),"run_id":self.run_id,"asset":asset,"contract_id":contract_id,"resolution_timestamp":resolution_timestamp.astimezone(UTC).isoformat(),"terminal_value":terminal_value,"reference":reference,"winning_side":winning,"entry_side":side,"classification_correct":None if side is None else side==winning,"resolution_source":resolution_source,"resolution_verification_status":"OFFICIAL_VERIFIED" if verified else "PROXY_RESOLUTION","data_quality_notes":"PROXY SETTLEMENT REFERENCE" if not verified else "OFFICIAL VERIFIED RESOLUTION","economic_result_status":"ECONOMIC_RESULT_UNAVAILABLE"}
  self.store.append("resolutions",record,"resolution_id"); self.events.emit(AppEvent(EventType.RESOLUTION,record["resolution_timestamp"],record)); return record
