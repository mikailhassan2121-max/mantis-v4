"""Manual-only orchestration across isolated specialist agents."""
from __future__ import annotations

import uuid

from ..agents.base import SpecialistAgent
from ..contracts import AgentContext, ExecutionMode, RiskDisposition, SupervisorResult
from ..events import AuditEvent, EventSink, NullEventSink
from ..consensus import ConsensusEngine
from ..registry import AgentRegistry
from ..ranking import rank_opportunities
from ..risk import RiskEngine


class MarketSupervisor:
    def __init__(self, agents: tuple[SpecialistAgent, ...], *, risk: RiskEngine | None = None,
                 events: EventSink | None = None):
        self.agents = agents
        self.registry = AgentRegistry(agents)
        self.consensus = ConsensusEngine(self.registry)
        self.risk = risk or RiskEngine()
        self.events = events or NullEventSink()

    def evaluate(self, context: AgentContext) -> SupervisorResult:
        run_id = str(uuid.uuid4())
        assessed = []
        benchmarks = []
        shadows = []
        errors = {}
        roles = {row.name: row.role for row in self.registry.descriptors}
        for agent in self.registry.agents:
            try:
                for candidate in agent.analyze(context):
                    if roles[agent.name] == "BENCHMARK":
                        benchmarks.append(candidate)
                    elif roles[agent.name] == "SHADOW":
                        shadows.append(candidate)
                    else:
                        assessed.append((candidate, self.risk.assess(candidate)))
            except Exception as exc:  # specialist failure is isolated and audited
                errors[agent.name] = f"{type(exc).__name__}: {exc}"
        ranked = rank_opportunities(assessed)
        allowed = tuple(row for row in ranked if row.risk.disposition is RiskDisposition.ALLOW_REVIEW)
        blocked = tuple(row for row in ranked if row.risk.disposition is RiskDisposition.BLOCK)
        consensus = self.consensus.summarize(allowed)
        result = SupervisorResult(run_id=run_id, observed_at=context.observed_at,
                                  execution_mode=ExecutionMode.MANUAL_ONLY,
                                  opportunities=allowed, blocked=blocked, agent_errors=errors,
                                  consensus=consensus, registry_manifest=self.registry.manifest(),
                                  benchmarks=tuple(benchmarks), shadows=tuple(shadows))
        evidence = []
        for row in ranked:
            evidence.append({
                "rank": row.rank, "rank_score": row.rank_score,
                "agent": row.candidate.agent, "agent_version": row.candidate.agent_version,
                "policy_version": row.candidate.policy_version,
                "market": row.candidate.market, "instrument": row.candidate.instrument,
                "contract_id": row.candidate.attributes.get("contract_id"),
                "window_end_utc": row.candidate.attributes.get("window_end_utc"),
                "side": row.candidate.side.value, "probability": row.candidate.probability,
                "conservative_probability": row.candidate.conservative_probability,
                "market_price": row.candidate.market_price,
                "economically_valid": row.candidate.economically_valid,
                "risk_disposition": row.risk.disposition.value,
                "risk_score": row.risk.score, "risk_reasons": list(row.risk.reasons),
                "role": "ADVISORY",
            })
        for candidate in benchmarks:
            evidence.append({
                "rank": None, "rank_score": None, "agent": candidate.agent,
                "agent_version": candidate.agent_version, "policy_version": candidate.policy_version,
                "market": candidate.market, "instrument": candidate.instrument,
                "contract_id": candidate.attributes.get("contract_id"),
                "window_end_utc": candidate.attributes.get("window_end_utc"),
                "side": candidate.side.value, "probability": candidate.probability,
                "conservative_probability": candidate.conservative_probability,
                "market_price": candidate.market_price, "economically_valid": False,
                "risk_disposition": "BENCHMARK_ONLY", "risk_score": None,
                "risk_reasons": [], "role": "BENCHMARK",
            })
        for candidate in shadows:
            evidence.append({
                "rank": None, "rank_score": None, "agent": candidate.agent,
                "agent_version": candidate.agent_version, "policy_version": candidate.policy_version,
                "market": candidate.market, "instrument": candidate.instrument,
                "contract_id": candidate.attributes.get("contract_id"),
                "window_end_utc": candidate.attributes.get("window_end_utc"),
                "side": candidate.side.value, "probability": candidate.probability,
                "conservative_probability": candidate.conservative_probability,
                "market_price": candidate.market_price, "economically_valid": False,
                "risk_disposition": "SHADOW_ONLY", "risk_score": None,
                "risk_reasons": [], "role": "SHADOW",
            })
        self.events.append(AuditEvent(str(uuid.uuid4()), "SUPERVISOR_EVALUATION",
                                     context.observed_at, run_id, {
                                         "execution_mode": result.execution_mode.value,
                                         "market": context.market,
                                         "instrument": context.instrument,
                                         "reviewable": len(allowed), "blocked": len(blocked),
                                         "benchmarks": len(benchmarks),
                                         "shadows": len(shadows),
                                         "agent_errors": errors, "candidates": evidence,
                                         "registry": dict(result.registry_manifest),
                                         "consensus": [{"contract_id": row.contract_id,
                                            "instrument": row.instrument, "status": row.status,
                                            "side": row.side.value, "probability_yes": row.probability_yes,
                                            "contributor_count": row.contributor_count,
                                            "independent_group_count": row.independent_group_count,
                                            "dispersion": row.dispersion, "agents": list(row.agents),
                                            "correlation_groups": list(row.correlation_groups),
                                            "policy_version": row.policy_version, "actionable": False}
                                           for row in consensus],
                                     }))
        return result
