"""Validated registry and capability manifest for advisory specialists."""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .agents.base import SpecialistAgent


@dataclass(frozen=True)
class AgentDescriptor:
    name: str
    version: str
    signal_family: str
    correlation_group: str
    supported_markets: tuple[str, ...]
    supported_instruments: tuple[str, ...]
    role: str
    execution_mode: str = "MANUAL_ONLY"


class AgentRegistry:
    version = "SVI_AGENT_REGISTRY_V2"
    allowed_roles = frozenset({"ADVISORY", "BENCHMARK", "SHADOW"})

    def __init__(self, agents: tuple[SpecialistAgent, ...]):
        if not agents:
            raise ValueError("at least one specialist is required")
        names = [str(agent.name) for agent in agents]
        if len(names) != len(set(names)):
            raise ValueError("specialist names must be unique")
        self._agents = tuple(agents)
        self._descriptors = tuple(self._describe(agent) for agent in agents)

    @staticmethod
    def _describe(agent: SpecialistAgent) -> AgentDescriptor:
        required = (agent.name, agent.version, agent.signal_family, agent.correlation_group)
        if not all(str(value).strip() for value in required):
            raise ValueError("specialist identity and correlation metadata are required")
        role = str(agent.role).strip().upper()
        if role not in AgentRegistry.allowed_roles:
            raise ValueError(f"unsupported specialist role: {role}")
        return AgentDescriptor(str(agent.name), str(agent.version), str(agent.signal_family),
                               str(agent.correlation_group), tuple(agent.supported_markets),
                               tuple(agent.supported_instruments), role)

    @property
    def agents(self) -> tuple[SpecialistAgent, ...]:
        return self._agents

    @property
    def descriptors(self) -> tuple[AgentDescriptor, ...]:
        return self._descriptors

    def manifest(self) -> dict:
        return {"registry_version": self.version, "specialist_count": len(self._agents),
                "execution_mode": "MANUAL_ONLY", "specialists": [asdict(row) for row in self._descriptors]}
