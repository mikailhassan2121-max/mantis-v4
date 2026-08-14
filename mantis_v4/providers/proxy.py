"""
MANTIS V4 — underlying-only proxy contract provider (last-resort fallback).

Master prompt section 26A names this ``UnderlyingOnlyFallbackProvider`` and
requires that MANTIS still run when nothing better is configured.

This provider reproduces V3's reference convention -- the Open of the first
1-minute bar at or after the window start -- but with one decisive difference:
it is TYPED as a proxy. ``ReferenceSource.PROXY_WINDOW_OPEN.is_verified`` is
False, so ``ContractSpec.economics_available`` is False no matter what else is
configured, and the UI is structurally unable to present this number as a
strike.

That is the Phase 2 fix for audit finding E-1. V3 computed exactly the same
number and then rendered it as ``BUFFER`` next to a column literally headed
``VALUE?``, with no way for the user to tell that the reference was a guess.

The settlement rule is ``PROXY_TERMINAL_ABOVE_REFERENCE``: it behaves
identically to strict-above for research labelling, but it is a distinct enum
member precisely so that no downstream code can mistake a proxy-settled label
for a venue-settled one.

Audit UNKNOWN-3 remains open: the real contract may settle against a TWAP, an
index, a different timestamp, or with different tie handling. Until that is
verified, every label produced through this provider is research-grade only.
"""

from __future__ import annotations

from typing import Callable, Optional

from ..clock import Instant
from ..contracts import (
    ContractQuote,
    ContractSpec,
    ContractWindow,
    ReferenceSource,
    SettlementRule,
)
from .base import BarSet, EventContractProvider, HealthState
from .market_data import price_at_or_after

BarLookup = Callable[[str], Optional[BarSet]]


class UnderlyingProxyContractProvider(EventContractProvider):
    """Derives an unverified reference from the underlying's own bars.

    Always last in the chain. Returns None only when the window's opening bar
    has not arrived yet -- in which case there is genuinely no reference, and
    MANTIS says so rather than substituting the current price (which is what V3
    did via ``anchor_ready = False``, silently producing a zero buffer).
    """

    name = "underlying-proxy"
    priority = 900

    def __init__(self, bar_lookup: BarLookup) -> None:
        super().__init__()
        self._bar_lookup = bar_lookup
        self.health.set_state(
            HealthState.DEGRADED, "proxy reference -- unverified, no economics"
        )

    def get_contract_spec(
        self,
        asset: str,
        window: ContractWindow,
        instant: Instant,
    ) -> Optional[ContractSpec]:
        try:
            bars = self._bar_lookup(asset)
        except Exception as exc:  # noqa: BLE001 - never crash the loop
            self.health.record_failure(f"{type(exc).__name__}: {exc}")
            return None

        if bars is None or bars.is_empty:
            self.health.set_state(HealthState.UNAVAILABLE, "no bars")
            return None

        found = price_at_or_after(bars.frame, window.start_utc)
        if found is None:
            # The window's first bar has not landed yet. There is no reference.
            # Returning None is the honest answer; the data-quality gate will
            # then refuse to trade, exactly as intended.
            self.health.set_state(
                HealthState.DEGRADED, "window opening bar not yet available"
            )
            return None

        reference, rule_name, bar_time = found

        # Guard against a reference drawn from a bar that belongs to a LATER
        # window. If the opening bar is missing entirely (a data gap at the
        # boundary), the "first bar at or after start" could be minutes late or
        # even past the resolution time. Using it would silently anchor this
        # contract to the wrong price.
        if bar_time >= window.end_utc:
            self.health.set_state(
                HealthState.UNAVAILABLE, "no bar inside window; refusing proxy"
            )
            return None

        self.health.set_state(
            HealthState.DEGRADED,
            f"proxy from {rule_name} @ {bar_time:%H:%M}Z",
        )

        return ContractSpec(
            window=window,
            underlying=asset,
            reference_price=reference,
            reference_source=ReferenceSource.PROXY_WINDOW_OPEN,
            settlement_rule=SettlementRule.PROXY_TERMINAL_ABOVE_REFERENCE,
            quote=ContractQuote(source="UNAVAILABLE"),
            provider_name=self.name,
            notes=(
                f"reference = {rule_name} of bar {bar_time.isoformat()} "
                "(UNVERIFIED proxy for the venue settlement reference)"
            ),
        )
