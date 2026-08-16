"""Fail-closed, research-only CF Benchmarks observation interface.

There is deliberately no Yahoo or other fallback in this module. Direct RTI
observations require separately authorized/licensed access.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Callable, Iterable

DIRECT_CF_DATA_STATUS = "UNAVAILABLE"
BENCHMARK_BY_ASSET = {
    "BTC-USD": "BRTI",
    "ETH-USD": "ETHUSD_RTI",
    "SOL-USD": "SOLUSD_RTI",
    "XRP-USD": "XRPUSD_RTI",
}


class CFDataUnavailable(RuntimeError):
    code = "CF_DATA_UNAVAILABLE"


@dataclass(frozen=True, order=True)
class CFObservation:
    timestamp_utc: datetime
    value: Decimal
    benchmark_id: str
    provenance: str = "CF_BENCHMARKS_DIRECT_LICENSED"

    def __post_init__(self):
        if self.timestamp_utc.tzinfo is None or self.timestamp_utc.utcoffset() is None:
            raise ValueError("CF observation timestamp must be timezone-aware")
        object.__setattr__(self, "timestamp_utc", self.timestamp_utc.astimezone(UTC))
        if self.benchmark_id not in BENCHMARK_BY_ASSET.values():
            raise ValueError("unknown CF benchmark identifier")
        if not self.value.is_finite() or self.value <= 0:
            raise ValueError("invalid CF observation value")


class CFBenchmarksResearchProvider:
    """Injected authorized retriever; absence fails rather than using a proxy."""

    def __init__(self, authorized_retriever: Callable | None = None):
        self._retriever = authorized_retriever

    def observations(self, *, asset: str, start_utc: datetime,
                     end_utc: datetime) -> tuple[CFObservation, ...]:
        benchmark = BENCHMARK_BY_ASSET.get(asset)
        if benchmark is None:
            raise ValueError("unsupported CF benchmark asset")
        if self._retriever is None:
            raise CFDataUnavailable("authorized/licensed CF RTI retrieval is not configured")
        if any(x.tzinfo is None or x.utcoffset() is None for x in (start_utc, end_utc)) or start_utc >= end_utc:
            raise ValueError("invalid UTC observation interval")
        rows = tuple(self._retriever(benchmark, start_utc.astimezone(UTC), end_utc.astimezone(UTC)))
        if any(not isinstance(row, CFObservation) or row.benchmark_id != benchmark for row in rows):
            raise ValueError("authorized retriever returned invalid benchmark observations")
        timestamps = [row.timestamp_utc for row in rows]
        if timestamps != sorted(timestamps) or len(timestamps) != len(set(timestamps)):
            raise ValueError("CF observations are unordered or duplicated")
        return rows
