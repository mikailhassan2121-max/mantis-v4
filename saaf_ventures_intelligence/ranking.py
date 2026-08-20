"""Deterministic ranking of reviewable opportunities."""
from __future__ import annotations

from .contracts import RankedOpportunity, RiskAssessment, SignalCandidate


def rank_opportunities(items: list[tuple[SignalCandidate, RiskAssessment]]) -> tuple[RankedOpportunity, ...]:
    scored = []
    for candidate, risk in items:
        edge = candidate.conservative_edge
        score = (edge if edge is not None else -1.0) - risk.score
        scored.append((candidate, risk, score))
    scored.sort(key=lambda item: (-item[2], item[0].agent, item[0].instrument))
    return tuple(RankedOpportunity(candidate, risk, score, index)
                 for index, (candidate, risk, score) in enumerate(scored, 1))
