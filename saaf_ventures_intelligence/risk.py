"""Advisory review gates; no sizing, portfolio, or execution logic."""
from __future__ import annotations

from dataclasses import dataclass

from .contracts import RiskAssessment, RiskDisposition, Side, SignalCandidate


@dataclass(frozen=True)
class RiskPolicy:
    version: str = "SVI_RISK_REVIEW_V1"
    require_economics: bool = True
    require_market_price: bool = True
    min_conservative_edge: float = 0.0


class RiskEngine:
    def __init__(self, policy: RiskPolicy | None = None):
        self.policy = policy or RiskPolicy()

    def assess(self, candidate: SignalCandidate) -> RiskAssessment:
        reasons = []
        if candidate.side is Side.ABSTAIN:
            reasons.append("SPECIALIST_ABSTAINED")
        if self.policy.require_economics and not candidate.economically_valid:
            reasons.append("ECONOMICS_NOT_VALIDATED")
        if self.policy.require_market_price and candidate.market_price is None:
            reasons.append("MARKET_PRICE_UNAVAILABLE")
        edge = candidate.conservative_edge
        if edge is not None and edge < self.policy.min_conservative_edge:
            reasons.append("CONSERVATIVE_EDGE_BELOW_REVIEW_FLOOR")
        disposition = RiskDisposition.BLOCK if reasons else RiskDisposition.ALLOW_REVIEW
        score = min(1.0, 0.25 * len(reasons))
        return RiskAssessment(disposition, score, tuple(reasons), self.policy.version)
