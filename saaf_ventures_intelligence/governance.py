"""Report-only admission governance for new SVI specialists."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AdmissionDecision:
    agent: str
    status: str
    reasons: tuple[str, ...]
    policy_version: str
    automatic_promotion: bool = False


class SpecialistAdmissionPolicy:
    """Evaluates evidence; it never mutates the registry or promotes an agent."""

    version = "SVI_SPECIALIST_ADMISSION_V3"

    def __init__(self, *, minimum_verified: int = 100, minimum_overlap: int = 50,
                 minimum_complementarity: float = 0.10, minimum_assets: int = 3):
        self.minimum_verified = int(minimum_verified)
        self.minimum_overlap = int(minimum_overlap)
        self.minimum_complementarity = float(minimum_complementarity)
        self.minimum_assets = int(minimum_assets)

    def evaluate(self, *, agent: str, role: str, verified_samples: int,
                 benchmark_overlap: int, brier_improvement: float | None,
                 log_loss_improvement: float | None,
                 complementarity: float | None,
                 brier_improvement_lower_bound: float | None = None,
                 log_loss_improvement_lower_bound: float | None = None,
                 recent_brier_improvement: float | None = None,
                 asset_coverage: int = 0, assets_meeting_minimum: int = 0,
                 worst_asset_brier_lower_bound: float | None = None) -> AdmissionDecision:
        reasons = []
        if str(role).upper() != "SHADOW":
            reasons.append("SPECIALIST_NOT_IN_SHADOW_ROLE")
        if verified_samples < self.minimum_verified:
            reasons.append("INSUFFICIENT_VERIFIED_OUTCOMES")
        if benchmark_overlap < self.minimum_overlap:
            reasons.append("INSUFFICIENT_BENCHMARK_OVERLAP")
        if brier_improvement is None or brier_improvement <= 0:
            reasons.append("NO_POSITIVE_BRIER_IMPROVEMENT")
        if log_loss_improvement is None or log_loss_improvement <= 0:
            reasons.append("NO_POSITIVE_LOG_LOSS_IMPROVEMENT")
        if brier_improvement_lower_bound is None or brier_improvement_lower_bound <= 0:
            reasons.append("BRIER_IMPROVEMENT_NOT_STATISTICALLY_ESTABLISHED")
        if log_loss_improvement_lower_bound is None or log_loss_improvement_lower_bound <= 0:
            reasons.append("LOG_LOSS_IMPROVEMENT_NOT_STATISTICALLY_ESTABLISHED")
        if recent_brier_improvement is None or recent_brier_improvement <= 0:
            reasons.append("RECENT_PERIOD_STABILITY_NOT_ESTABLISHED")
        if asset_coverage < self.minimum_assets:
            reasons.append("INSUFFICIENT_ASSET_COVERAGE")
        if assets_meeting_minimum < self.minimum_assets:
            reasons.append("INSUFFICIENT_PER_ASSET_SAMPLE_COVERAGE")
        if worst_asset_brier_lower_bound is None or worst_asset_brier_lower_bound <= 0:
            reasons.append("CROSS_ASSET_ROBUSTNESS_NOT_ESTABLISHED")
        if complementarity is None or complementarity < self.minimum_complementarity:
            reasons.append("COMPLEMENTARITY_NOT_ESTABLISHED")
        status = "ELIGIBLE_FOR_HUMAN_REVIEW" if not reasons else "NOT_ELIGIBLE"
        return AdmissionDecision(str(agent), status, tuple(reasons), self.version, False)
