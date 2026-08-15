from __future__ import annotations
import csv,math
from pathlib import Path
import numpy as np
from ..models.evaluation import clustered_accuracy
from .store import ForwardStore
def sample_label(n):
 return "VERY SMALL SAMPLE" if n<30 else "SMALL SAMPLE" if n<100 else "EARLY EVIDENCE" if n<300 else "MEANINGFUL FORWARD SAMPLE" if n<1000 else "LARGER FORWARD SAMPLE"
def _bucket(x,edges):
 for lo,hi,label in edges:
  if lo<=x<hi:return label
 return edges[-1][2]
def forward_report(store:ForwardStore,n_boot=400):
 obs=store.read("observations"); entries=store.read("entries"); resolutions=store.read("resolutions")
 rmap={(x["contract_id"],x["asset"]):x for x in resolutions}; resolved=[{**e,**rmap[(e["contract_id"],e["asset"])]} for e in entries if (e["contract_id"],e["asset"]) in rmap and rmap[(e["contract_id"],e["asset"])].get("classification_correct") is not None]
 total=len({(x.get("contract_id"),x.get("asset")) for x in obs}); n=len(entries)
 out={"total_contracts_observed":total,"total_entry_events":n,"abstention_rate":1-n/total if total else 1.,"sample_label":sample_label(len(resolved)),"resolved_entries":len(resolved),"economic_results_verified":sum(x.get("economic_result_status")=="ECONOMIC_RESULT_VERIFIED" for x in resolutions)}
 health=store.read("provider_health")
 out["provider_reliability"]={"health_records":len(health),"successful_fetches":max([int(x.get("successful_fetches",0)) for x in health],default=0),"failed_fetches":max([int(x.get("failed_fetches",0)) for x in health],default=0),"stale_observations":sum(float(x.get("data_age_seconds",0))>90 for x in obs),"data_holds":sum(x.get("phase6_state")=="DATA HOLD" for x in obs),"out_of_order_records":sum(obs[i].get("timestamp_utc","")>obs[i+1].get("timestamp_utc","") for i in range(len(obs)-1))}
 if not resolved:return out
 correct=np.array([x["classification_correct"] for x in resolved],float); groups=np.array([x["contract_id"] for x in resolved]); ci=clustered_accuracy(correct,groups,n_boot=n_boot)
 out.update(classification_accuracy=ci.point,ci_low=ci.low,ci_high=ci.high)
 def stats(rows): return {"n":len(rows),"accuracy":sum(x["classification_correct"] for x in rows)/len(rows) if rows else None}
 out["yes"]=stats([x for x in resolved if x["side"]=="YES"]); out["no"]=stats([x for x in resolved if x["side"]=="NO"])
 out["per_asset"]={a:stats([x for x in resolved if x["asset"]==a]) for a in sorted({x["asset"] for x in resolved})}
 def sliced(field,edges):
  labels={label:[] for _,_,label in edges}
  for x in resolved: labels[_bucket(float(x[field]),edges)].append(x)
  return {k:stats(v) for k,v in labels.items() if v}
 out["accuracy_by_probability"]=sliced("model_probability",[(0,.90,"<.90"),(.90,.95,".90-.95"),(.95,.97,".95-.97"),(.97,1.01,".97+")])
 out["accuracy_by_lower_bound"]=sliced("lower_bound",[(0,.85,"<.85"),(.85,.90,".85-.90"),(.90,.95,".90-.95"),(.95,1.01,".95+")])
 out["accuracy_by_fragility"]=sliced("fragility",[(0,25,"0-25"),(25,50,"25-50"),(50,75,"50-75"),(75,101,"75+")])
 out["accuracy_by_disagreement"]=sliced("disagreement",[(0,.02,"0-.02"),(.02,.05,".02-.05"),(.05,.1,".05-.10"),(.1,2,".10+")])
 out["accuracy_by_crossing_risk"]=sliced("crossing_probability",[(0,.1,"0-.10"),(.1,.2,".10-.20"),(.2,.35,".20-.35"),(.35,2,".35+")])
 out["entry_time_distribution"]={}
 for x in entries:
  b=_bucket(float(x["seconds_remaining"]),[(0,30,"0-30s"),(30,60,"30-60s"),(60,120,"60-120s"),(120,180,"120-180s"),(180,300,"180-300s"),(300,901,"300s+")]); out["entry_time_distribution"][b]=out["entry_time_distribution"].get(b,0)+1
 return out
def compare_historical(report:dict,observations:list[dict]):
 n=report.get("resolved_entries",0)
 entries=[o for o in observations if o.get("phase6_state") in ("ENTER YES","ENTER NO")]
 context={"yes_share":sum(o.get("predicted_side")=="YES" for o in entries)/len(entries) if entries else None,
  "asset_distribution":{a:sum(o.get("asset")==a for o in entries) for a in sorted({o.get("asset") for o in entries})},
  "mean_probability":float(np.mean([max(o.get("p_yes",.5),o.get("p_no",.5)) for o in entries])) if entries else None,
  "mean_fragility":float(np.mean([o.get("fragility",0) for o in entries])) if entries else None,
  "volatility_regimes":{r:sum(o.get("metadata",{}).get("volatility_regime")==r for o in entries) for r in sorted({o.get("metadata",{}).get("volatility_regime") for o in entries}) if r},
  "cross_asset_correlation_status":"REQUIRES_SUFFICIENT_SYNCHRONOUS_FORWARD_RETURNS"}
 if n<30:return {"status":"FORWARD_SAMPLE_TOO_SMALL","historical_accuracy":.9658,"historical_coverage":.6373,**context}
 warnings=[]
 if report.get("classification_accuracy") is not None and abs(report["classification_accuracy"]-.9658)>.05:warnings.append("ACCURACY_SHIFT")
 if abs((1-report.get("abstention_rate",1))-.6373)>.15:warnings.append("COVERAGE_SHIFT")
 return {"status":"FORWARD_REGIME_SHIFT_WARNING" if warnings else "FORWARD_REGIME_NORMAL","warnings":warnings,"historical_accuracy":.9658,"historical_coverage":.6373,**context}
def export_csv(store:ForwardStore,out_dir:Path):
 out_dir=Path(out_dir); out_dir.mkdir(parents=True,exist_ok=True); paths=[]
 for name in store.FILES:
  rows=store.read(name)
  if not rows:continue
  keys=sorted({k for r in rows for k in r if not isinstance(r.get(k),(dict,list))}); path=out_dir/f"{name}.csv"
  with path.open("w",newline="",encoding="utf-8") as f:
   w=csv.DictWriter(f,fieldnames=keys,extrasaction="ignore"); w.writeheader(); w.writerows(rows)
  paths.append(path)
 return paths
