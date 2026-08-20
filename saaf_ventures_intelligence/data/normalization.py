"""Immutable normalization and provenance for already-collected market observations."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import math
from types import MappingProxyType
from typing import Any, Mapping

from ..contracts import AgentContext


@dataclass(frozen=True)
class MarketObservation:
    contract_id: str
    market: str
    instrument: str
    observed_at: datetime
    horizon_seconds: float
    window_end_utc: str | None
    target: float | None
    proxy_current: float | None
    yes_bid: float | None
    yes_ask: float | None
    no_bid: float | None
    no_ask: float | None
    identity_valid: bool
    reference_valid: bool
    quote_valid: bool
    quote_verified: bool
    source: str
    provenance: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "provenance", MappingProxyType(dict(self.provenance)))


@dataclass(frozen=True)
class DataQualityReport:
    policy_version: str
    input_rows: int
    normalized_rows: int
    rejected_rows: int
    reference_capable: int
    quote_capable: int
    rejection_reasons: tuple[str, ...]
    mutates_source: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class NormalizedBatch:
    observations: tuple[MarketObservation, ...]
    quality: DataQualityReport


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _quote_valid(values: tuple[float | None, ...], verified: bool) -> bool:
    if not verified or any(value is None or not 0 <= value <= 1 for value in values):
        return False
    yes_bid, yes_ask, no_bid, no_ask = values
    return bool(yes_bid <= yes_ask and no_bid <= no_ask and
                abs(((yes_bid+yes_ask)/2)+(no_bid+no_ask)/2-1.0) <= .10)


def normalize_context(context: AgentContext) -> NormalizedBatch:
    selection = context.payload.get("selection")
    rows = selection.get("candidates") or () if isinstance(selection, Mapping) else ()
    observations = []
    rejections = []
    for row in rows:
        if not isinstance(row, Mapping):
            rejections.append("ROW_NOT_MAPPING"); continue
        contract_id = str(row.get("contract_id") or "").strip()
        instrument = str(row.get("asset") or "").strip()
        horizon = _number(row.get("seconds_remaining"))
        if not contract_id or not instrument or horizon is None or horizon <= 0:
            rejections.append("IDENTITY_OR_HORIZON_INVALID"); continue
        target = _number(row.get("target")); current = _number(row.get("proxy_current"))
        reference_valid = bool(target is not None and current is not None and target > 0 and current > 0)
        quotes = tuple(_number(row.get(name)) for name in ("yes_bid","yes_ask","no_bid","no_ask"))
        verified = row.get("quote_verified") is True
        observations.append(MarketObservation(
            contract_id, context.market, instrument, context.observed_at, horizon,
            str(row.get("window_end_utc")) if row.get("window_end_utc") else None,
            target, current, *quotes, True, reference_valid, _quote_valid(quotes,verified), verified,
            str(row.get("event_market_source") or row.get("event_market_provider") or "UNKNOWN"),
            {"reference":str(row.get("reference_policy") or "UNKNOWN"),
             "quote":str(row.get("quote_provenance") or "UNKNOWN"),
             "model":str(row.get("model_policy") or "UNKNOWN")}))
    quality = DataQualityReport("SVI_DATA_NORMALIZATION_V1",len(rows),len(observations),
        len(rejections),sum(row.reference_valid for row in observations),
        sum(row.quote_valid for row in observations),tuple(sorted(rejections)),False)
    return NormalizedBatch(tuple(observations),quality)
