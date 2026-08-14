"""
MANTIS V4 — provider fallback chain.

Master prompt section 26A fixes the preference order:

    1. OfficialWebullOpenAPIProvider        (authenticated, verified)
    2. authorized local bridge / user feed  (LocalFileContractProvider)
    3. manual local JSON / CSV              (same adapter)
    4. UnderlyingOnlyFallbackProvider       (UnderlyingProxyContractProvider)

The chain asks each provider in priority order and returns the FIRST spec that
is usable. "Usable" deliberately means more than "not None": a spec with no
reference price is worse than nothing, because it would let a downstream caller
believe a contract was resolved when it was not.

No provider failure escapes this class. Section 26N: a failed Webull request, a
failed Yahoo request, one bad coin, malformed JSON, or a stale quote must never
crash the program.
"""

from __future__ import annotations

from typing import Optional, Sequence

from ..clock import Instant
from ..contracts import ContractSpec, ContractWindow
from .base import EventContractProvider, HealthState, ProviderHealth


class ContractProviderChain:
    """Ordered fallback across event-contract providers."""

    def __init__(self, providers: Sequence[EventContractProvider]) -> None:
        self.providers: list[EventContractProvider] = sorted(
            providers, key=lambda p: p.priority
        )
        self.last_served_by: dict[str, str] = {}

    def get_contract_spec(
        self,
        asset: str,
        window: ContractWindow,
        instant: Instant,
    ) -> Optional[ContractSpec]:
        for provider in self.providers:
            try:
                spec = provider.get_contract_spec(asset, window, instant)
            except Exception as exc:  # noqa: BLE001 - section 26N
                provider.health.record_failure(f"{type(exc).__name__}: {exc}")
                continue

            if spec is None:
                continue
            if not spec.has_reference:
                # A spec without a reference cannot settle anything. Keep going
                # rather than returning a shell that looks like success.
                continue

            self.last_served_by[asset] = provider.name
            return spec

        self.last_served_by.pop(asset, None)
        return None

    # -- introspection for the status panel ---------------------------------

    def health_report(self) -> list[ProviderHealth]:
        return [provider.health for provider in self.providers]

    def best_available_state(self) -> HealthState:
        for provider in self.providers:
            if provider.health.state.is_usable:
                return provider.health.state
        return HealthState.UNAVAILABLE

    def economics_provider_available(self) -> bool:
        """Whether ANY provider could supply verified contract economics.

        Only a verified provider counts. The proxy provider is permanently
        excluded by construction: its reference source is not verified.
        """
        for provider in self.providers:
            if provider.priority < 900 and provider.health.state.is_usable:
                return True
        return False
