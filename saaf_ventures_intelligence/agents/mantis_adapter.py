"""Lossless adapter for MANTIS manual-signal output.

The adapter does not invoke, tune, or reproduce MANTIS math. It translates the
already-authoritative selection dictionary and retains policy/risk telemetry.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from mantis_v4.manual_signal.core import V22_POLICY

from ..contracts import AgentContext, Side, SignalCandidate
from .base import SpecialistAgent


class MantisAdapter(SpecialistAgent):
    name = "MANTIS"
    version = "4.x"
    signal_family = "CRYPTO_EVENT_PROBABILITY"
    correlation_group = "MANTIS_MODEL_FAMILY"
    supported_markets = ("KALSHI",)
    supported_instruments = ("BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD")

    def analyze(self, context: AgentContext) -> tuple[SignalCandidate, ...]:
        selection = context.payload.get("selection")
        if not isinstance(selection, Mapping):
            return ()
        rows = selection.get("candidates") or ()
        candidates = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            candidates.append(self._translate(row, context.observed_at, context.market))
        return tuple(candidates)

    def _translate(self, row: Mapping[str, Any], observed_at: datetime, market: str) -> SignalCandidate:
        raw_side = str(row.get("side") or "ABSTAIN").upper()
        side = Side(raw_side) if raw_side in ("YES", "NO") else Side.ABSTAIN
        confidence = float(row.get("confidence") or 0.0)
        conservative = float(row.get("conservative_probability") or 0.0)
        ask = row.get("ask")
        attributes = dict(row)
        attributes.update({
            "adapter_semantics": "LOSSLESS_PRESENTATION_ONLY",
            "manual_only": True,
            "historical_policy_preserved": True,
        })
        return SignalCandidate(
            agent=self.name,
            agent_version=self.version,
            policy_version=str(row.get("policy_version") or V22_POLICY.version),
            market=market,
            instrument=str(row.get("asset") or "UNKNOWN"),
            side=side,
            probability=confidence,
            conservative_probability=conservative,
            market_price=float(ask) if ask is not None else None,
            horizon_seconds=float(row["seconds_remaining"]) if row.get("seconds_remaining") is not None else None,
            observed_at=observed_at,
            explanation=str(row.get("reason") or row.get("status") or "MANTIS advisory candidate"),
            economically_valid=bool(row.get("economically_valid", False)),
            source_status=str(row.get("quote_status") or row.get("status") or "UNKNOWN"),
            attributes=attributes,
        )
