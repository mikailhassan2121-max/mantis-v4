"""Read-only SVI payload integration point for the existing command center."""
from __future__ import annotations

from ..contracts import RankedOpportunity, SupervisorResult
from dataclasses import asdict


def _row(item: RankedOpportunity) -> dict:
    candidate = item.candidate
    return {
        "rank": item.rank, "rank_score": item.rank_score,
        "agent": candidate.agent, "agent_version": candidate.agent_version,
        "policy_version": candidate.policy_version, "market": candidate.market,
        "instrument": candidate.instrument, "side": candidate.side.value,
        "probability": candidate.probability,
        "conservative_probability": candidate.conservative_probability,
        "market_price": candidate.market_price, "conservative_edge": candidate.conservative_edge,
        "risk": {"disposition": item.risk.disposition.value, "score": item.risk.score,
                 "reasons": list(item.risk.reasons), "policy_version": item.risk.policy_version},
        "explanation": candidate.explanation,
    }


def _benchmark(candidate) -> dict:
    return {"agent":candidate.agent,"agent_version":candidate.agent_version,
            "policy_version":candidate.policy_version,"market":candidate.market,
            "instrument":candidate.instrument,"side":candidate.side.value,
            "probability":candidate.probability,
            "probability_yes":candidate.attributes.get("probability_yes"),
            "source_status":candidate.source_status,"role":"BENCHMARK",
            "actionable":False}


def _shadow(candidate) -> dict:
    return {"agent": candidate.agent, "agent_version": candidate.agent_version,
            "policy_version": candidate.policy_version, "market": candidate.market,
            "instrument": candidate.instrument, "side": candidate.side.value,
            "probability": candidate.probability, "source_status": candidate.source_status,
            "role": "SHADOW", "actionable": False, "promotion": "HUMAN_REVIEW_REQUIRED"}


def command_center_payload(result: SupervisorResult, evidence: dict | None = None) -> dict:
    payload = {
        "product": "SAAF VENTURES INTELLIGENCE",
        "phase": "MULTI_SPECIALIST_FOUNDATION",
        "execution_mode": result.execution_mode.value,
        "observation_only": True,
        "run_id": result.run_id,
        "observed_at": result.observed_at.isoformat(),
        "opportunities": [_row(item) for item in result.opportunities],
        "blocked": [_row(item) for item in result.blocked],
        "agent_errors": dict(result.agent_errors),
        "registry": dict(result.registry_manifest),
        "consensus": [{**asdict(row), "side": row.side.value} for row in result.consensus],
        "benchmarks": [_benchmark(row) for row in result.benchmarks],
        "shadows": [_shadow(row) for row in result.shadows],
    }
    if evidence is not None:
        payload["evidence"] = dict(evidence)
    return payload


def publish_to_mantis(state, result: SupervisorResult, evidence: dict | None = None) -> dict:
    """Attach SVI output to MANTIS state without granting browser write access."""
    payload = command_center_payload(result, evidence)
    state.set_svi(payload)
    return payload
