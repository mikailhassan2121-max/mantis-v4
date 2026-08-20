"""Shared, typed contracts used by every SVI specialist and supervisor."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Optional

UTC = timezone.utc


class Side(str, Enum):
    YES = "YES"
    NO = "NO"
    ABSTAIN = "ABSTAIN"


class ExecutionMode(str, Enum):
    """Phase 1 deliberately exposes no executable mode."""
    MANUAL_ONLY = "MANUAL_ONLY"


class RiskDisposition(str, Enum):
    ALLOW_REVIEW = "ALLOW_REVIEW"
    BLOCK = "BLOCK"


def _probability(name: str, value: float) -> float:
    value = float(value)
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be in [0, 1]")
    return value


@dataclass(frozen=True)
class AgentContext:
    observed_at: datetime
    market: str
    instrument: str
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


@dataclass(frozen=True)
class SignalCandidate:
    agent: str
    agent_version: str
    policy_version: str
    market: str
    instrument: str
    side: Side
    probability: float
    conservative_probability: float
    market_price: Optional[float]
    horizon_seconds: Optional[float]
    observed_at: datetime
    explanation: str
    economically_valid: bool = False
    source_status: str = "UNKNOWN"
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "probability", _probability("probability", self.probability))
        object.__setattr__(self, "conservative_probability", _probability(
            "conservative_probability", self.conservative_probability))
        if self.market_price is not None:
            object.__setattr__(self, "market_price", _probability("market_price", self.market_price))
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        object.__setattr__(self, "attributes", MappingProxyType(dict(self.attributes)))

    @property
    def conservative_edge(self) -> Optional[float]:
        if self.market_price is None or self.side is Side.ABSTAIN:
            return None
        return self.conservative_probability - self.market_price


@dataclass(frozen=True)
class RiskAssessment:
    disposition: RiskDisposition
    score: float
    reasons: tuple[str, ...]
    policy_version: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "score", _probability("risk score", self.score))


@dataclass(frozen=True)
class RankedOpportunity:
    candidate: SignalCandidate
    risk: RiskAssessment
    rank_score: float
    rank: int


@dataclass(frozen=True)
class SupervisorResult:
    run_id: str
    observed_at: datetime
    execution_mode: ExecutionMode
    opportunities: tuple[RankedOpportunity, ...]
    blocked: tuple[RankedOpportunity, ...]
    agent_errors: Mapping[str, str] = field(default_factory=dict)
    consensus: tuple[Any, ...] = ()
    registry_manifest: Mapping[str, Any] = field(default_factory=dict)
    benchmarks: tuple[SignalCandidate, ...] = ()
    shadows: tuple[SignalCandidate, ...] = ()

    def __post_init__(self) -> None:
        if self.execution_mode is not ExecutionMode.MANUAL_ONLY:
            raise ValueError("SVI Phase 1 supports manual-only execution")
        object.__setattr__(self, "agent_errors", MappingProxyType(dict(self.agent_errors)))
        object.__setattr__(self, "registry_manifest", MappingProxyType(dict(self.registry_manifest)))
