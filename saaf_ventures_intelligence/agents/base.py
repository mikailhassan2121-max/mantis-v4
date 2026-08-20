"""Standard specialist interface. Agents analyze; they never execute."""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..contracts import AgentContext, SignalCandidate


class SpecialistAgent(ABC):
    name: str
    version: str
    signal_family: str = "UNSPECIFIED"
    correlation_group: str = "UNSPECIFIED"
    supported_markets: tuple[str, ...] = ()
    supported_instruments: tuple[str, ...] = ()
    role: str = "ADVISORY"

    @abstractmethod
    def analyze(self, context: AgentContext) -> tuple[SignalCandidate, ...]:
        """Return immutable advisory candidates without external side effects."""
        raise NotImplementedError
