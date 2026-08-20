"""Report-only temporal drift diagnostics for resolved probability forecasts."""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable, TYPE_CHECKING

if TYPE_CHECKING:
    from ..outcomes import ResolvedForecast


def _brier(rows) -> float:
    return sum((row.probability_yes-float(row.outcome_yes))**2 for row in rows)/len(rows)


def temporal_drift_report(resolved: Iterable["ResolvedForecast"], *, minimum_sample: int = 30,
                          brier_tolerance: float = .05, probability_tolerance: float = .20) -> dict:
    groups=defaultdict(list)
    for row in resolved: groups[(row.agent,row.policy_version,row.role)].append(row)
    reports=[]
    for (agent,policy,role),group in sorted(groups.items()):
        rows=sorted(group,key=lambda row:(row.resolution_timestamp,row.contract_id))
        split=len(rows)//2; early=rows[:split]; recent=rows[split:]
        if len(rows)<minimum_sample or not early or not recent:
            reports.append({"agent":agent,"policy_version":policy,"role":role,
                "sample_size":len(rows),"status":"INSUFFICIENT_EVIDENCE",
                "early_brier":None,"recent_brier":None,"brier_deterioration":None,
                "mean_probability_shift":None,"actionable":False})
            continue
        early_brier=_brier(early); recent_brier=_brier(recent)
        early_mean=sum(row.probability_yes for row in early)/len(early)
        recent_mean=sum(row.probability_yes for row in recent)/len(recent)
        deterioration=recent_brier-early_brier; probability_shift=abs(recent_mean-early_mean)
        status="ALERT" if deterioration>brier_tolerance or probability_shift>probability_tolerance else "PASS"
        reports.append({"agent":agent,"policy_version":policy,"role":role,
            "sample_size":len(rows),"status":status,"early_brier":early_brier,
            "recent_brier":recent_brier,"brier_deterioration":deterioration,
            "mean_probability_shift":probability_shift,"brier_tolerance":brier_tolerance,
            "probability_tolerance":probability_tolerance,"actionable":False})
    return {"policy_version":"SVI_TEMPORAL_DRIFT_V1","minimum_sample":minimum_sample,
        "reports":reports,"automatic_action":False,"read_only":True}
