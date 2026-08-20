"""Read-only integrity, manifest, and reporting operations for SVI evidence."""
from __future__ import annotations

import hashlib
from pathlib import Path

from .events import read_events
from .outcomes import resolved_evidence_report
from .replay import EvidenceReplay

FORBIDDEN_FIELDS = frozenset({
    "order_id", "brokerage_account", "portfolio_size", "position_size",
    "authentication_token", "api_key", "private_key",
})


def _hash(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def evidence_manifest(evidence_path: Path, resolution_path: Path) -> dict:
    events = read_events(evidence_path)
    replay = EvidenceReplay(evidence_path).summary()
    schema_versions = sorted({row.get("schema_version") for row in events})
    policies = sorted({str(candidate.get("policy_version"))
                       for row in events for candidate in (row.get("payload") or {}).get("candidates") or ()
                       if candidate.get("policy_version")})
    agents = sorted({str(candidate.get("agent"))
                     for row in events for candidate in (row.get("payload") or {}).get("candidates") or ()
                     if candidate.get("agent")})
    data_policies = sorted({str((row.get("payload") or {}).get("data_quality",{}).get("policy_version"))
                            for row in events
                            if (row.get("payload") or {}).get("data_quality",{}).get("policy_version")})
    return {
        "manifest_version": "SVI_EVIDENCE_MANIFEST_V1",
        "evidence_file": {"file": Path(evidence_path).name, "rows": len(events), "sha256": _hash(Path(evidence_path))},
        "resolution_file": {"file": Path(resolution_path).name, "sha256": _hash(Path(resolution_path))},
        "schema_versions": schema_versions,
        "agents": agents, "policy_versions": policies,
        "data_policy_versions": data_policies,
        "first_timestamp": replay.first_timestamp, "last_timestamp": replay.last_timestamp,
        "evaluations": replay.evaluations, "candidates": replay.candidates,
        "execution_mode": "MANUAL_ONLY", "read_only": True,
    }


def audit_evidence(evidence_path: Path, resolution_path: Path) -> dict:
    errors = []
    warnings = []
    try:
        events = read_events(evidence_path)
    except (OSError, ValueError) as exc:
        return {"status": "FAIL", "errors": [f"EVIDENCE_READ:{type(exc).__name__}:{exc}"],
                "warnings": [], "read_only": True}
    ids = [str(row.get("event_id")) for row in events]
    if len(ids) != len(set(ids)):
        errors.append("DUPLICATE_EVENT_ID")
    previous = None
    contract_count = 0
    for row in events:
        stamp = str(row.get("timestamp") or "")
        if previous is not None and stamp < previous:
            errors.append("NON_CHRONOLOGICAL_EVENT")
        previous = stamp
        if row.get("schema_version") not in {1, 2, 3, 4, 5, 6}:
            errors.append("UNSUPPORTED_SCHEMA_VERSION")
        if row.get("event_type") != "SUPERVISOR_EVALUATION":
            continue
        payload = row.get("payload") or {}
        if payload.get("execution_mode") != "MANUAL_ONLY":
            errors.append("EXECUTION_MODE_NOT_MANUAL")
        if row.get("schema_version") >= 6:
            quality = payload.get("data_quality") or {}
            if quality.get("policy_version") != "SVI_DATA_NORMALIZATION_V1":
                errors.append("DATA_QUALITY_POLICY_MISSING")
            if quality.get("mutates_source") is not False:
                errors.append("DATA_NORMALIZATION_MUST_NOT_MUTATE_SOURCE")
        registry = payload.get("registry") or {}
        if row.get("schema_version") >= 3 and registry.get("execution_mode") != "MANUAL_ONLY":
            errors.append("REGISTRY_EXECUTION_MODE_NOT_MANUAL")
        for summary in payload.get("consensus") or ():
            if summary.get("actionable") is not False:
                errors.append("CONSENSUS_MUST_BE_NON_ACTIONABLE")
        if row.get("schema_version") >= 4:
            roles = {specialist.get("name"):specialist.get("role") for specialist in registry.get("specialists") or ()}
            if any(role not in {"ADVISORY", "BENCHMARK", "SHADOW"} for role in roles.values()):
                errors.append("INVALID_SPECIALIST_ROLE")
            consensus_agents = {agent for summary in payload.get("consensus") or () for agent in summary.get("agents") or ()}
            if any(roles.get(agent) == "BENCHMARK" for agent in consensus_agents):
                errors.append("BENCHMARK_INCLUDED_IN_CONSENSUS")
            if any(roles.get(agent) == "SHADOW" for agent in consensus_agents):
                errors.append("SHADOW_INCLUDED_IN_CONSENSUS")
        for candidate in payload.get("candidates") or ():
            contract_count += 1
            if not candidate.get("contract_id"):
                if row.get("schema_version") == 1:
                    warnings.append("LEGACY_V1_CANDIDATE_WITHOUT_CONTRACT_ID")
                else:
                    errors.append("MISSING_CONTRACT_ID")
            if not candidate.get("agent") or not candidate.get("policy_version"):
                errors.append("MISSING_AGENT_POLICY_IDENTITY")
            if FORBIDDEN_FIELDS.intersection(candidate):
                errors.append("FORBIDDEN_EXECUTION_FIELD")
            if row.get("schema_version") >= 4 and candidate.get("role") not in {"ADVISORY", "BENCHMARK", "SHADOW"}:
                errors.append("INVALID_CANDIDATE_ROLE")
            if row.get("schema_version") >= 5 and candidate.get("role") == "SHADOW":
                if candidate.get("rank") is not None or candidate.get("risk_disposition") != "SHADOW_ONLY":
                    errors.append("SHADOW_MUST_BE_UNRANKED")
            if row.get("schema_version") >= 5 and candidate.get("role") == "BENCHMARK":
                if candidate.get("rank") is not None or candidate.get("risk_disposition") != "BENCHMARK_ONLY":
                    errors.append("BENCHMARK_MUST_BE_UNRANKED")
    try:
        resolved = resolved_evidence_report(evidence_path, resolution_path)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        errors.append(f"RESOLUTION_JOIN:{type(exc).__name__}:{exc}")
        resolved = None
    if not events:
        warnings.append("NO_SVI_EVIDENCE_YET")
    if events and resolved is not None and not resolved.get("resolved_forecasts"):
        warnings.append("NO_VERIFIED_RESOLVED_FORECASTS_YET")
    return {"status": "PASS" if not errors else "FAIL", "errors": sorted(set(errors)),
            "warnings": warnings, "events": len(events), "candidate_records": contract_count,
            "resolved_forecasts": None if resolved is None else resolved["resolved_forecasts"],
            "read_only": True, "orders_submitted": 0}


def operational_report(evidence_path: Path, resolution_path: Path) -> dict:
    return {
        "report_version": "SVI_OPERATIONAL_REPORT_V1",
        "audit": audit_evidence(evidence_path, resolution_path),
        "manifest": evidence_manifest(evidence_path, resolution_path),
        "replay": EvidenceReplay(evidence_path).summary().__dict__,
        "evidence": resolved_evidence_report(evidence_path, resolution_path),
        "manual_only": True, "read_only": True,
    }
