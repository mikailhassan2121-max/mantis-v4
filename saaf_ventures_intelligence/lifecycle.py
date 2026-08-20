"""Read-only specialist lifecycle roster derived from immutable evidence."""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable


def specialist_lifecycle_report(events: Iterable[dict], evidence: dict) -> dict:
    histories=defaultdict(list); latest={}
    for event in events:
        registry=(event.get("payload") or {}).get("registry") or {}
        for specialist in registry.get("specialists") or ():
            name=str(specialist.get("name") or "")
            role=str(specialist.get("role") or "UNKNOWN")
            if not name: continue
            latest[name]=dict(specialist)
            if not histories[name] or histories[name][-1]["role"]!=role:
                histories[name].append({"role":role,"timestamp":event.get("timestamp"),
                    "registry_version":registry.get("registry_version")})
    decisions={row.get("agent"):row for row in evidence.get("admission_governance") or ()}
    roster=[]
    for name,specialist in sorted(latest.items()):
        role=specialist.get("role")
        decision=decisions.get(name)
        if role=="SHADOW": state=(decision or {}).get("status","INSUFFICIENT_EVIDENCE")
        elif role=="BENCHMARK": state="BENCHMARK_ONLY"
        else: state="CONFIGURED_ADVISORY"
        roster.append({"name":name,"version":specialist.get("version"),"role":role,
            "state":state,"admission_policy":None if decision is None else decision.get("policy_version"),
            "automatic_transition":False,"role_history":histories[name]})
    return {"policy_version":"SVI_SPECIALIST_LIFECYCLE_V1","specialists":roster,
        "automatic_promotion":False,"automatic_demotion":False,
        "configuration_change_required":True,"read_only":True}
