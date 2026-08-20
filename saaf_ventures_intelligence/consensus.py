"""Correlation-aware consensus diagnostics; never an execution decision."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .contracts import RankedOpportunity, Side
from .registry import AgentRegistry


@dataclass(frozen=True)
class ConsensusSummary:
    contract_id: str
    instrument: str
    status: str
    side: Side
    probability_yes: float | None
    contributor_count: int
    independent_group_count: int
    dispersion: float | None
    agents: tuple[str, ...]
    correlation_groups: tuple[str, ...]
    policy_version: str = "SVI_CONSENSUS_DIAGNOSTIC_V1"
    actionable: bool = False


class ConsensusEngine:
    def __init__(self, registry: AgentRegistry, *, max_cross_side_dispersion: float = .15):
        self.registry = registry
        self.max_cross_side_dispersion = float(max_cross_side_dispersion)
        self._groups = {row.name: row.correlation_group for row in registry.descriptors}

    def summarize(self, opportunities: tuple[RankedOpportunity, ...]) -> tuple[ConsensusSummary, ...]:
        by_contract = defaultdict(list)
        for row in opportunities:
            candidate = row.candidate
            contract_id = str(candidate.attributes.get("contract_id") or candidate.instrument)
            by_contract[(contract_id, candidate.instrument)].append(candidate)
        output = []
        for (contract_id, instrument), candidates in sorted(by_contract.items()):
            family_values = defaultdict(list)
            for candidate in candidates:
                p_yes = candidate.probability if candidate.side is Side.YES else 1.0 - candidate.probability
                family_values[self._groups[candidate.agent]].append(p_yes)
            independent = [sum(values) / len(values) for values in family_values.values()]
            probability_yes = sum(independent) / len(independent)
            raw = [value for values in family_values.values() for value in values]
            dispersion = max(raw) - min(raw) if len(raw) > 1 else 0.0
            crosses = min(raw) < .5 < max(raw) if len(raw) > 1 else False
            if crosses and dispersion > self.max_cross_side_dispersion:
                status, side = "ABSTAIN_DISAGREEMENT", Side.ABSTAIN
            else:
                status = "PASS_THROUGH" if len(candidates) == 1 else "AGREEMENT_DIAGNOSTIC"
                side = Side.YES if probability_yes >= .5 else Side.NO
            output.append(ConsensusSummary(contract_id, instrument, status, side, probability_yes,
                          len(candidates), len(independent), dispersion,
                          tuple(sorted(candidate.agent for candidate in candidates)),
                          tuple(sorted(family_values))))
        return tuple(output)
