"""Immutable joins between SVI forecasts and verified contract resolutions."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
import json
from pathlib import Path
import math

from .events import read_events
from .governance import SpecialistAdmissionPolicy
from .probabilities import CalibrationObservation, evaluate_calibration


def _log_loss(probability: float, outcome: bool) -> float:
    value = max(1e-15, min(1.0 - 1e-15, probability))
    return -(float(outcome) * math.log(value) + (1.0-float(outcome)) * math.log(1.0-value))


def _mean_lower_95(values: list[float]) -> tuple[float, float | None]:
    mean = sum(values) / len(values)
    if len(values) < 2:
        return mean, None
    variance = sum((value-mean)**2 for value in values) / (len(values)-1)
    return mean, mean - 1.96 * math.sqrt(variance / len(values))


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
        resolved_at = str(resolution.get("resolved_at_utc") or "")
        try:
            forecast_time = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
            resolution_time = datetime.fromisoformat(resolved_at.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if forecast_time >= resolution_time:
            continue
        confidence = float(candidate["probability"])
        probability_yes = confidence if candidate["side"] == "YES" else 1.0 - confidence
        resolved.append(ResolvedForecast(
            contract_id, agent, policy, str(candidate.get("instrument") or "UNKNOWN"),
            str(timestamp), probability_yes, resolution["result"] == "YES",
            resolved_at,
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
    asset_comparisons = []
    comparison_groups = defaultdict(list)
    for row in resolved:
        if row.role in {"ADVISORY", "SHADOW"} and row.contract_id in benchmark_by_contract:
            comparison_groups[(row.agent, row.policy_version, row.role)].append(row)
    for (agent, policy, role), rows in sorted(comparison_groups.items()):
        brier_deltas=[]; log_deltas=[]
        for row in rows:
            benchmark=benchmark_by_contract[row.contract_id]; outcome=float(row.outcome_yes)
            brier_deltas.append((benchmark.probability_yes-outcome)**2-(row.probability_yes-outcome)**2)
            log_deltas.append(_log_loss(benchmark.probability_yes,row.outcome_yes)-
                              _log_loss(row.probability_yes,row.outcome_yes))
        brier_improvement,brier_lower=_mean_lower_95(brier_deltas)
        log_improvement,log_lower=_mean_lower_95(log_deltas)
        recent_start=len(brier_deltas)//2
        recent_brier=sum(brier_deltas[recent_start:])/len(brier_deltas[recent_start:])
        minimum_asset_sample=max(1,minimum_sample//4)
        scorecards=[]
        for instrument in sorted({row.instrument for row in rows}):
            asset_rows=[row for row in rows if row.instrument==instrument]
            asset_brier=[]; asset_log=[]
            for row in asset_rows:
                benchmark=benchmark_by_contract[row.contract_id]; outcome=float(row.outcome_yes)
                asset_brier.append((benchmark.probability_yes-outcome)**2-(row.probability_yes-outcome)**2)
                asset_log.append(_log_loss(benchmark.probability_yes,row.outcome_yes)-
                                 _log_loss(row.probability_yes,row.outcome_yes))
            asset_brier_mean,asset_brier_lower=_mean_lower_95(asset_brier)
            asset_log_mean,asset_log_lower=_mean_lower_95(asset_log)
            card={"agent":agent,"policy_version":policy,"role":role,"instrument":instrument,
                "sample_size":len(asset_rows),"minimum_sample":minimum_asset_sample,
                "sample_qualified":len(asset_rows)>=minimum_asset_sample,
                "brier_improvement":asset_brier_mean,"brier_improvement_lower_95":asset_brier_lower,
                "log_loss_improvement":asset_log_mean,"log_loss_improvement_lower_95":asset_log_lower}
            scorecards.append(card); asset_comparisons.append(card)
        qualified=[card for card in scorecards if card["sample_qualified"]]
        qualified_lowers=[card["brier_improvement_lower_95"] for card in qualified
                          if card["brier_improvement_lower_95"] is not None]
        comparisons.append({"agent":agent,"policy_version":policy,"role":role,
            "benchmark_agent":benchmark_by_contract[rows[0].contract_id].agent,"overlap":len(rows),
            "status":"REPORT_ONLY" if len(rows)>=minimum_sample else "INSUFFICIENT_EVIDENCE",
            "brier_improvement":brier_improvement,
            "brier_improvement_lower_95":brier_lower,
            "log_loss_improvement":log_improvement,
            "log_loss_improvement_lower_95":log_lower,
            "recent_half_brier_improvement":recent_brier,
            "asset_coverage":len({row.instrument for row in rows}),
            "minimum_per_asset_sample":minimum_asset_sample,
            "assets_meeting_minimum":len(qualified),
            "worst_asset_brier_lower_95":min(qualified_lowers) if len(qualified_lowers)==len(qualified) and qualified else None,
            "uncertainty_method":"PAIRED_NORMAL_APPROXIMATION_95"})
    advisory_by_contract = {row.contract_id: row for row in resolved if row.role == "ADVISORY"}
    complementarity = []
    shadow_groups = defaultdict(list)
    for row in resolved:
        if row.role == "SHADOW" and row.contract_id in advisory_by_contract:
            shadow_groups[(row.agent, row.policy_version)].append(row)
    for (agent, policy), rows in sorted(shadow_groups.items()):
        distances = [abs(row.probability_yes-advisory_by_contract[row.contract_id].probability_yes)
                     for row in rows]
        complementarity.append({"agent":agent,"policy_version":policy,"overlap":len(rows),
            "mean_absolute_probability_difference":sum(distances)/len(distances),
            "metric":"MATCHED_MEAN_ABSOLUTE_PROBABILITY_DIFFERENCE",
            "quality_claim":False})
    governance = []
    admission = SpecialistAdmissionPolicy(minimum_verified=minimum_sample,
        minimum_overlap=minimum_sample)
    for (agent, policy), rows in sorted(shadow_groups.items()):
        comparison = next((row for row in comparisons
            if row["agent"] == agent and row["policy_version"] == policy), None)
        diagnostic = next((row for row in complementarity
            if row["agent"] == agent and row["policy_version"] == policy), None)
        decision = admission.evaluate(agent=agent, role="SHADOW", verified_samples=len(rows),
            benchmark_overlap=0 if comparison is None else comparison["overlap"],
            brier_improvement=None if comparison is None else comparison["brier_improvement"],
            log_loss_improvement=None if comparison is None else comparison["log_loss_improvement"],
            complementarity=None if diagnostic is None else diagnostic["mean_absolute_probability_difference"],
            brier_improvement_lower_bound=None if comparison is None else comparison["brier_improvement_lower_95"],
            log_loss_improvement_lower_bound=None if comparison is None else comparison["log_loss_improvement_lower_95"],
            recent_brier_improvement=None if comparison is None else comparison["recent_half_brier_improvement"],
            asset_coverage=0 if comparison is None else comparison["asset_coverage"],
            assets_meeting_minimum=0 if comparison is None else comparison["assets_meeting_minimum"],
            worst_asset_brier_lower_bound=None if comparison is None else comparison["worst_asset_brier_lower_95"])
        governance.append(asdict(decision))
    return {
        "status": "REPORT_ONLY",
        "resolution_requirement": "OFFICIAL_VERIFIED_ONLY",
        "forecast_grain": "LATEST_PRE_RESOLUTION_PER_AGENT_POLICY_CONTRACT",
        "resolved_forecasts": len(resolved),
        "unresolved_forecasts": unresolved,
        "groups": reports,
        "benchmark_comparisons": comparisons,
        "asset_comparisons": asset_comparisons,
        "complementarity": complementarity,
        "admission_governance": governance,
        "automatic_promotion": False,
        "model_activation": False,
    }
