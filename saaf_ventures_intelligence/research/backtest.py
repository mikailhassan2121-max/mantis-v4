"""Deterministic, read-only historical scoring over verified SVI forecasts."""
from __future__ import annotations

from collections import defaultdict
import math
from pathlib import Path

from ..outcomes import ResolvedForecast, join_verified_forecasts


def _score(rows: list[ResolvedForecast]) -> dict:
    if not rows:
        return {"sample_size":0,"brier_score":None,"log_loss":None,"directional_accuracy":None}
    epsilon=1e-15
    brier=sum((row.probability_yes-float(row.outcome_yes))**2 for row in rows)/len(rows)
    log_loss=-sum(float(row.outcome_yes)*math.log(max(epsilon,row.probability_yes))+
        (1.0-float(row.outcome_yes))*math.log(max(epsilon,1.0-row.probability_yes)) for row in rows)/len(rows)
    accuracy=sum((row.probability_yes>=.5)==row.outcome_yes for row in rows)/len(rows)
    return {"sample_size":len(rows),"brier_score":brier,"log_loss":log_loss,
            "directional_accuracy":accuracy}


def _folds(rows: list[ResolvedForecast], fold_count: int) -> list[dict]:
    count=min(max(1,int(fold_count)),len(rows))
    output=[]
    for index in range(count):
        start=index*len(rows)//count; end=(index+1)*len(rows)//count
        output.append({"fold":index+1,"chronological_start":rows[start].resolution_timestamp,
            "chronological_end":rows[end-1].resolution_timestamp,**_score(rows[start:end])})
    return output


def historical_replay_report(evidence_path: Path, resolution_path: Path, *, fold_count: int = 5) -> dict:
    resolved,unresolved=join_verified_forecasts(evidence_path,resolution_path)
    groups=defaultdict(list)
    for row in resolved: groups[(row.agent,row.policy_version,row.role)].append(row)
    reports=[]; attribution=[]
    for (agent,policy,role),group_rows in sorted(groups.items()):
        rows=sorted(group_rows,key=lambda row:(row.resolution_timestamp,row.contract_id))
        folds=_folds(rows,fold_count)
        reports.append({"agent":agent,"policy_version":policy,"role":role,**_score(rows),
            "folds":folds,"worst_fold_brier":max((row["brier_score"] for row in folds),default=None),
            "best_fold_brier":min((row["brier_score"] for row in folds),default=None),
            "fitted":False,"actionable":False})
        slices=defaultdict(list)
        for row in rows:
            side="YES" if row.probability_yes>=.5 else "NO"
            slices[(row.instrument,side)].append(row)
        for (instrument,side),slice_rows in sorted(slices.items()):
            attribution.append({"agent":agent,"policy_version":policy,"role":role,
                "instrument":instrument,"forecast_side":side,**_score(slice_rows)})
    return {"report_version":"SVI_HISTORICAL_REPLAY_V1","resolved_forecasts":len(resolved),
        "unresolved_forecasts":unresolved,"forecast_grain":"LATEST_PRE_RESOLUTION",
        "chronological_folds":True,"model_fitting":False,"policy_tuning":False,
        "simulated_trading":False,"pnl_calculated":False,"reports":reports,
        "attribution":attribution,"read_only":True}
