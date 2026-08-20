"""Fixed reference-distance heuristic for evidence-only shadow evaluation."""
from __future__ import annotations

import math
from typing import Mapping

from ..contracts import AgentContext, Side, SignalCandidate
from .base import SpecialistAgent


class ReferenceDistanceShadow(SpecialistAgent):
    """Independent baseline using no MANTIS probability and no contract quote."""

    name = "REFERENCE_DISTANCE_SHADOW"
    version = "1"
    signal_family = "REFERENCE_DISTANCE_HEURISTIC"
    correlation_group = "REFERENCE_DISTANCE_BASELINE"
    supported_markets = ("KALSHI",)
    supported_instruments = ("BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD")
    role = "SHADOW"
    policy_version = "REFERENCE_DISTANCE_LOGISTIC_V1"
    scale_bps = 5.0

    def analyze(self, context: AgentContext) -> tuple[SignalCandidate, ...]:
        selection = context.payload.get("selection")
        if not isinstance(selection, Mapping):
            return ()
        output = []
        for row in selection.get("candidates") or ():
            if not isinstance(row, Mapping):
                continue
            try:
                target = float(row["target"])
                current = float(row["proxy_current"])
                seconds = float(row["seconds_remaining"])
            except (KeyError, TypeError, ValueError):
                continue
            contract_id = row.get("contract_id")
            if (not contract_id or not math.isfinite(target) or not math.isfinite(current)
                    or not math.isfinite(seconds) or target <= 0 or seconds <= 0):
                continue
            distance_bps = 10000.0 * (current - target) / target
            probability_yes = 1.0 / (1.0 + math.exp(-max(-20.0, min(20.0, distance_bps / self.scale_bps))))
            probability_yes = max(.05, min(.95, probability_yes))
            side = Side.YES if probability_yes >= .5 else Side.NO
            confidence = probability_yes if side is Side.YES else 1.0 - probability_yes
            attributes = {
                "contract_id": str(contract_id),
                "window_end_utc": row.get("window_end_utc"),
                "distance_bps": distance_bps,
                "probability_yes": probability_yes,
                "input_fields": ("target", "proxy_current", "seconds_remaining"),
                "uses_mantis_probability": False,
                "uses_kalshi_quote": False,
                "heuristic_uncalibrated": True,
                "shadow_only": True,
                "actionable": False,
            }
            output.append(SignalCandidate(
                self.name, self.version, self.policy_version, context.market,
                str(row.get("asset") or "UNKNOWN"), side, confidence, confidence,
                None, seconds, context.observed_at,
                "Fixed reference-distance logistic heuristic; evidence only",
                False, "SHADOW_HEURISTIC", attributes))
        return tuple(output)
