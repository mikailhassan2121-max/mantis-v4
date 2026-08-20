"""Immutable joins between SVI forecasts and verified contract resolutions."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
import json
from pathlib import Path

from .events import read_events
from .probabilities import CalibrationObservation, evaluate_calibration


@dataclass(frozen=True)
class ResolvedForecast:
    contract_id: str
    agent: str
    policy_version: str
    instrument: str
    forecast_timestamp: str
    probability_yes: float
    outcome_yes: bool
    resolution_timestamp: str
    resolution_source: str
    role: str = "ADVISORY"


def _read_jsonl(path: Path) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    rows = []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            if number == len(lines):
                break
            raise ValueError(f"malformed {path.name} line {number}")
        if not isinstance(value, dict):
            raise ValueError(f"malformed {path.name} line {number}")
        rows.append(value)
    return rows


def join_verified_forecasts(evidence_path: Path, resolution_path: Path) -> tuple[tuple[ResolvedForecast, ...], int]:
    """Join latest forecasts to VERIFIED results; proxy/mismatched rows are excluded."""
    latest = {}
    for event in read_events(evidence_path):
        if event.get("event_type") != "SUPERVISOR_EVALUATION":
            continue
        timestamp = event.get("timestamp")
        for candidate in (event.get("payload") or {}).get("candidates") or ():
            contract_id = candidate.get("contract_id")
            if not contract_id or candidate.get("side") not in {"YES", "NO"}:
                continue
            key = (str(contract_id), str(candidate.get("agent")), str(candidate.get("policy_version")))
            previous = latest.get(key)
            if previous is None or str(timestamp) >= str(previous[0]):
                latest[key] = (timestamp, candidate)
    verified = {str(row["contract_id"]): row for row in _read_jsonl(resolution_path)
                if row.get("contract_id") and row.get("resolution_status") == "VERIFIED"
                and row.get("result") in {"YES", "NO"}}
    resolved = []
    for (contract_id, agent, policy), (timestamp, candidate) in latest.items():
        resolution = verified.get(contract_id)
        if resolution is None:
            continue
        confidence = float(candidate["probability"])
        probability_yes = confidence if candidate["side"] == "YES" else 1.0 - confidence
        resolved.append(ResolvedForecast(
            contract_id, agent, policy, str(candidate.get("instrument") or "UNKNOWN"),
            str(timestamp), probability_yes, resolution["result"] == "YES",
            str(resolution.get("resolved_at_utc") or ""),
            str(resolution.get("settlement_source") or "UNKNOWN"),
            str(candidate.get("role") or "ADVISORY"),
        ))
    resolved.sort(key=lambda row: (row.resolution_timestamp, row.contract_id, row.agent, row.policy_version))
    return tuple(resolved), max(0, len(latest) - len(resolved))


def resolved_evidence_report(evidence_path: Path, resolution_path: Path, *, minimum_sample: int = 30) -> dict:
    resolved, unresolved = join_verified_forecasts(evidence_path, resolution_path)
    groups = defaultdict(list)
    for row in resolved:
        groups[(row.agent, row.policy_version)].append(
            CalibrationObservation(row.probability_yes, row.outcome_yes, row.agent, row.policy_version))
    reports = []
    for (agent, policy), observations in sorted(groups.items()):
        report = evaluate_calibration(observations, minimum_sample=minimum_sample)
        reports.append({"agent": agent, "policy_version": policy, **asdict(report)})
    benchmark_by_contract = {row.contract_id: row for row in resolved if row.role == "BENCHMARK"}
    comparisons = []
    advisory_groups = defaultdict(list)
    for row in resolved:
        if row.role == "ADVISORY" and row.contract_id in benchmark_by_contract:
            advisory_groups[(row.agent, row.policy_version)].append(row)
    for (agent, policy), rows in sorted(advisory_groups.items()):
        agent_obs = [CalibrationObservation(row.probability_yes,row.outcome_yes,agent,policy) for row in rows]
        benchmark_obs = [CalibrationObservation(benchmark_by_contract[row.contract_id].probability_yes,
                         row.outcome_yes,benchmark_by_contract[row.contract_id].agent,
                         benchmark_by_contract[row.contract_id].policy_version) for row in rows]
        agent_report=evaluate_calibration(agent_obs,minimum_sample=minimum_sample)
        benchmark_report=evaluate_calibration(benchmark_obs,minimum_sample=minimum_sample)
        comparisons.append({"agent":agent,"policy_version":policy,
            "benchmark_agent":benchmark_by_contract[rows[0].contract_id].agent,"overlap":len(rows),
            "status":"REPORT_ONLY" if len(rows)>=minimum_sample else "INSUFFICIENT_EVIDENCE",
            "brier_improvement":benchmark_report.brier_score-agent_report.brier_score,
            "log_loss_improvement":benchmark_report.log_loss-agent_report.log_loss})
    return {
        "status": "REPORT_ONLY",
        "resolution_requirement": "OFFICIAL_VERIFIED_ONLY",
        "forecast_grain": "LATEST_PRE_RESOLUTION_PER_AGENT_POLICY_CONTRACT",
        "resolved_forecasts": len(resolved),
        "unresolved_forecasts": unresolved,
        "groups": reports,
        "benchmark_comparisons": comparisons,
        "model_activation": False,
    }
