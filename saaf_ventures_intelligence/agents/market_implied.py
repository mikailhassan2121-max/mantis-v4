"""Anonymous Kalshi quote midpoint benchmark; never an opportunity generator."""
from __future__ import annotations

from typing import Mapping

from ..contracts import AgentContext, Side, SignalCandidate
from .base import SpecialistAgent


class KalshiMarketImpliedBenchmark(SpecialistAgent):
    name = "KALSHI_MARKET_IMPLIED"
    version = "1"
    signal_family = "MARKET_IMPLIED_BENCHMARK"
    correlation_group = "KALSHI_ORDERBOOK"
    supported_markets = ("KALSHI",)
    supported_instruments = ("BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD")
    role = "BENCHMARK"
    policy_version = "KALSHI_MIDPOINT_BENCHMARK_V1"

    def analyze(self, context: AgentContext) -> tuple[SignalCandidate, ...]:
        selection = context.payload.get("selection")
        if not isinstance(selection, Mapping):
            return ()
        output = []
        for row in selection.get("candidates") or ():
            if not isinstance(row, Mapping) or row.get("quote_verified") is not True:
                continue
            point, discrepancy = self._probability_yes(row)
            if point is None or discrepancy > .10:
                continue
            side = Side.YES if point >= .5 else Side.NO
            confidence = point if side is Side.YES else 1.0 - point
            attributes = dict(row)
            attributes.update({"benchmark_only": True, "actionable": False,
                               "probability_yes": point, "complement_discrepancy": discrepancy})
            output.append(SignalCandidate(
                self.name, self.version, self.policy_version, context.market,
                str(row.get("asset") or "UNKNOWN"), side, confidence, confidence,
                confidence, float(row["seconds_remaining"]) if row.get("seconds_remaining") is not None else None,
                context.observed_at, "Anonymous Kalshi bid/ask midpoint benchmark",
                False, "VERIFIED_QUOTE_BENCHMARK", attributes))
        return tuple(output)

    @staticmethod
    def _probability_yes(row: Mapping) -> tuple[float | None, float]:
        estimates = []
        if row.get("yes_bid") is not None and row.get("yes_ask") is not None:
            estimates.append((float(row["yes_bid"]) + float(row["yes_ask"])) / 2.0)
        if row.get("no_bid") is not None and row.get("no_ask") is not None:
            estimates.append(1.0 - (float(row["no_bid"]) + float(row["no_ask"])) / 2.0)
        if not estimates or any(not 0 <= value <= 1 for value in estimates):
            return None, 1.0
        discrepancy = max(estimates) - min(estimates)
        return sum(estimates) / len(estimates), discrepancy
