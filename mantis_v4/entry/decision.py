"""Interpretable Phase 6 entry policy around the unchanged Normal-Z anchor.

This module never modifies probability.  Diagnostics decide eligibility only.
Without contract quotes every result is classification research and EV is
explicitly unavailable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Any, Optional


class Decision(str, Enum):
    WAIT = "WAIT"
    ENTER_YES = "ENTER YES"
    ENTER_NO = "ENTER NO"
    NO_TRADE = "NO TRADE THIS CONTRACT"
    DATA_HOLD = "DATA HOLD"


@dataclass(frozen=True)
class ProbabilityState:
    p_yes: float
    conservative_bound: float

    @property
    def p_no(self) -> float:
        return 1.0 - self.p_yes

    @property
    def confidence(self) -> float:
        return max(self.p_yes, self.p_no)

    @property
    def side(self) -> str:
        return "YES" if self.p_yes >= 0.5 else "NO"


@dataclass(frozen=True)
class RiskDiagnostics:
    buffer_z: float
    fragility: float
    disagreement: float
    crossing_probability: float
    volatility_regime: str = "UNKNOWN"
    crossings: int = 0
    trend_developing: bool = False


@dataclass(frozen=True)
class DecisionInputs:
    asset: str
    contract_id: str
    seconds_remaining: float
    probability: ProbabilityState
    risk: RiskDiagnostics
    data_fresh: bool = True
    reference_valid: bool = True
    contract_valid: bool = True
    rollover_detected: bool = False
    sufficient_history: bool = True
    diagnostics_consistent: bool = True
    economics: Optional[Any] = None


@dataclass(frozen=True)
class DecisionPolicy:
    name: str
    probability_threshold: float = 0.80
    lcb_threshold: Optional[float] = None
    max_fragility: Optional[float] = None
    max_disagreement: Optional[float] = None
    max_crossing_probability: Optional[float] = None
    max_seconds_remaining: float = 900.0
    min_seconds_remaining: float = 0.0
    min_abs_z: float = 0.0
    early_wait_seconds: float = 0.0
    no_trade_seconds: float = 0.0
    max_crossings: Optional[int] = None


@dataclass(frozen=True)
class DecisionResult:
    decision: Decision
    reason_code: str
    reasons: tuple[str, ...]
    policy_name: str
    side: Optional[str]
    ev_status: str = "UNAVAILABLE"
    metadata: dict = field(default_factory=dict)


def _invalid(i: DecisionInputs) -> Optional[str]:
    p, r, s = i.probability, i.risk, i.seconds_remaining
    if not i.data_fresh:
        return "STALE_DATA"
    if not i.reference_valid:
        return "MISSING_REFERENCE"
    if not i.contract_valid or not i.contract_id:
        return "INVALID_CONTRACT_ID"
    if i.rollover_detected:
        return "CONTRACT_ROLLOVER"
    if not i.sufficient_history:
        return "INSUFFICIENT_HISTORY"
    values = (s, p.p_yes, p.conservative_bound, r.buffer_z, r.fragility,
              r.disagreement, r.crossing_probability)
    if not all(math.isfinite(v) for v in values):
        return "NONFINITE_INPUT"
    if not (0.0 <= s <= 900.0):
        return "INVALID_TIME_REMAINING"
    if not (0 <= p.p_yes <= 1 and 0 <= p.conservative_bound <= p.confidence + 1e-12):
        return "INCONSISTENT_PROBABILITY"
    if not (0 <= r.fragility <= 100 and 0 <= r.disagreement <= 1 and
            0 <= r.crossing_probability <= 1) or not i.diagnostics_consistent:
        return "INCONSISTENT_DIAGNOSTICS"
    return None


def decide(i: DecisionInputs, policy: DecisionPolicy) -> DecisionResult:
    """Apply a deterministic policy; gates abstain but never alter Normal-Z."""
    bad = _invalid(i)
    if bad:
        return DecisionResult(Decision.DATA_HOLD, bad, (bad.replace("_", " "),),
                              policy.name, None)
    p, r, sec = i.probability, i.risk, i.seconds_remaining
    if sec <= policy.no_trade_seconds:
        return DecisionResult(Decision.NO_TRADE, "ENTRY_WINDOW_EXPIRED",
                              ("entry window expired",), policy.name, None)
    if policy.max_crossings is not None and r.crossings > policy.max_crossings:
        return DecisionResult(Decision.NO_TRADE, "CHOP_UNSTABLE",
                              ("reference crossing behavior is unstable",), policy.name, None)
    if sec > policy.max_seconds_remaining or sec > policy.early_wait_seconds > 0:
        return DecisionResult(Decision.WAIT, "EARLY_INSUFFICIENT_INFORMATION",
                              ("contract is too early for this policy",), policy.name, None)
    if sec < policy.min_seconds_remaining:
        return DecisionResult(Decision.NO_TRADE, "TOO_LATE",
                              ("classification entry window has closed",), policy.name, None)
    if p.confidence < policy.probability_threshold:
        return DecisionResult(Decision.WAIT, "PROBABILITY_TOO_LOW",
                              ("Normal-Z confidence is below threshold",), policy.name, None)
    if policy.lcb_threshold is not None and p.conservative_bound < policy.lcb_threshold:
        return DecisionResult(Decision.WAIT, "CONSERVATIVE_BOUND_TOO_LOW",
                              ("conservative probability bound is below threshold",), policy.name, None)
    if abs(r.buffer_z) < policy.min_abs_z:
        return DecisionResult(Decision.WAIT, "BUFFER_TOO_SMALL",
                              ("normalized reference buffer is too small",), policy.name, None)
    if policy.max_fragility is not None and r.fragility > policy.max_fragility:
        return DecisionResult(Decision.WAIT, "FRAGILITY_TOO_HIGH",
                              ("fragility is above threshold",), policy.name, None)
    if policy.max_disagreement is not None and r.disagreement > policy.max_disagreement:
        return DecisionResult(Decision.WAIT, "DISAGREEMENT_TOO_HIGH",
                              ("model disagreement is above threshold",), policy.name, None)
    if (policy.max_crossing_probability is not None and
            r.crossing_probability > policy.max_crossing_probability):
        return DecisionResult(Decision.WAIT, "CROSSING_RISK_TOO_HIGH",
                              ("reference crossing risk is above threshold",), policy.name, None)
    if r.trend_developing:
        return DecisionResult(Decision.WAIT, "TREND_BUFFER_DEVELOPING",
                              ("trend/reference buffer is still developing",), policy.name, None)
    side = p.side
    reasons = (f"Normal-Z confidence {p.confidence:.1%}",
               f"conservative bound {p.conservative_bound:.1%}",
               f"fragility {r.fragility:.1f}", f"disagreement {r.disagreement:.3f}")
    return DecisionResult(Decision.ENTER_YES if side == "YES" else Decision.ENTER_NO,
                          "ELIGIBLE", reasons, policy.name, side)
