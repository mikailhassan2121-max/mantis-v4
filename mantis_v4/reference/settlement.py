"""Exact-event specifications and research-only settlement measurements."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Iterable

import numpy as np
import pandas as pd

from .cf_benchmarks import BENCHMARK_BY_ASSET, CFObservation

SETTLEMENT_SPEC_VERSION = "KALSHI_SETTLEMENT_EVENT_V1"
REFERENCE_VALIDATION_VERSION = "CF_REFERENCE_VALIDATION_V1"
ENDING_RECONSTRUCTION_VERSION = "ENDING_60S_RECONSTRUCTION_V1"
STATUS = "RESEARCH_SHADOW_ONLY"
KALSHI_CRYPTO_RULES_URL = "https://help.kalshi.com/en/articles/13823838-crypto-markets"
CF_METHODOLOGY_URL = "https://docs.cfbenchmarks.com/CME%20CF%20Real%20Indices%20Methodology.pdf"

SERIES_BY_ASSET = {"BTC-USD": "KXBTC15M", "ETH-USD": "KXETH15M",
                   "SOL-USD": "KXSOL15M", "XRP-USD": "KXXRP15M"}


@dataclass(frozen=True)
class SettlementEventSpec:
    asset: str
    series_ticker: str
    benchmark_source: str
    benchmark_identifier: str
    target_definition: str
    ending_window_seconds: int
    observation_frequency_seconds: int
    expected_observation_count: int
    aggregation_rule: str
    final_round_digits: int
    comparison_operator: str
    equality_outcome: str
    provenance_url: str
    retrieval_timestamp_utc: str
    verification_status: str


def event_specifications(retrieved_at: datetime) -> dict[str, SettlementEventSpec]:
    if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
        raise ValueError("retrieval timestamp must be timezone-aware")
    stamp = retrieved_at.astimezone(UTC).isoformat()
    digits = {"BTC-USD": 2, "ETH-USD": 2, "SOL-USD": 4, "XRP-USD": 4}
    return {asset: SettlementEventSpec(
        asset, series, "CF_BENCHMARKS", BENCHMARK_BY_ASSET[asset],
        "Official Kalshi floor_strike; current rules may express it as the starting 60-second CF RTI average",
        60, 1, 60, "ARITHMETIC_MEAN_THEN_MARKET_SPECIFIED_ROUNDING", digits[asset], ">=", "YES",
        KALSHI_CRYPTO_RULES_URL, stamp, "PARTIALLY_VERIFIED")
        for asset, series in SERIES_BY_ASSET.items()}


@dataclass(frozen=True)
class EndingAverageResult:
    benchmark_identifier: str
    window_start_utc: datetime
    window_end_utc: datetime
    expected_observations: int
    actual_observations: int
    first_timestamp_utc: datetime
    last_timestamp_utc: datetime
    ending_average: Decimal
    terminal_observation: Decimal
    target: Decimal
    outcome_yes: bool


def reconstruct_ending_average(observations: Iterable[CFObservation], *,
                               window_end_utc: datetime, target: Decimal,
                               expected_count: int = 60) -> EndingAverageResult:
    """Reconstruct [end-60s, end) only when a complete 1Hz sequence exists."""
    rows = tuple(observations)
    if window_end_utc.tzinfo is None or window_end_utc.utcoffset() is None:
        raise ValueError("settlement boundary must be timezone-aware")
    end = window_end_utc.astimezone(UTC); start = end - timedelta(seconds=expected_count)
    if expected_count != 60 or not target.is_finite() or target <= 0:
        raise ValueError("invalid ending-average parameters")
    if len(rows) != expected_count:
        raise ValueError("incomplete ending window")
    timestamps = [row.timestamp_utc for row in rows]
    if timestamps != sorted(timestamps) or len(set(timestamps)) != len(timestamps):
        raise ValueError("ending observations unordered or duplicated")
    expected = [start + timedelta(seconds=i) for i in range(expected_count)]
    if timestamps != expected:
        raise ValueError("ending observations do not exactly cover [end-60s,end)")
    if len({row.benchmark_id for row in rows}) != 1:
        raise ValueError("mixed benchmark identifiers")
    average = sum((row.value for row in rows), Decimal("0")) / Decimal(expected_count)
    return EndingAverageResult(rows[0].benchmark_id, start, end, expected_count, len(rows),
                               timestamps[0], timestamps[-1], average, rows[-1].value,
                               target, average >= target)


def signed_error_bps(proxy, official):
    proxy, official = np.asarray(proxy, float), np.asarray(official, float)
    if len(proxy) != len(official) or not len(proxy) or not np.isfinite(np.c_[proxy, official]).all() or np.any(official <= 0):
        raise ValueError("invalid paired reference values")
    return 10000.0 * (proxy - official) / official


def side_flip(proxy, official, target):
    proxy, official, target = map(lambda x: np.asarray(x, float), (proxy, official, target))
    if not (len(proxy) == len(official) == len(target)) or not np.isfinite(np.c_[proxy, official, target]).all():
        raise ValueError("invalid side-flip inputs")
    return (proxy >= target) != (official >= target)


def paired_error_metrics(frame: pd.DataFrame, *, proxy_col: str, official_col: str,
                         target_col: str) -> dict:
    if not {proxy_col, official_col, target_col} <= set(frame) or frame.empty:
        raise ValueError("paired official/proxy observations required")
    bps = signed_error_bps(frame[proxy_col], frame[official_col]); absolute = np.abs(bps)
    proxy_values = frame[proxy_col].to_numpy(float); official_values = frame[official_col].to_numpy(float)
    correlation = (float(np.corrcoef(proxy_values, official_values)[0, 1])
                   if proxy_values.std() > 0 and official_values.std() > 0 else None)
    return {"n": int(len(frame)), "mean_signed_bps": float(bps.mean()),
            "median_signed_bps": float(np.median(bps)), "median_absolute_bps": float(np.median(absolute)),
            "mean_absolute_bps": float(absolute.mean()), "std_bps": float(bps.std(ddof=0)),
            "p90_absolute_bps": float(np.quantile(absolute, .90)),
            "p95_absolute_bps": float(np.quantile(absolute, .95)),
            "p99_absolute_bps": float(np.quantile(absolute, .99)),
            "max_absolute_bps": float(absolute.max()),
            "correlation": correlation,
            "target_side_flip_rate": float(side_flip(frame[proxy_col], frame[official_col], frame[target_col]).mean())}


def classify_actual_reference(*, proxy_value: Decimal | None, official_value: Decimal | None,
                              target: Decimal) -> str:
    if proxy_value is None or official_value is None:
        return "REFERENCE_UNKNOWN"
    if (proxy_value >= target) != (official_value >= target):
        return "REFERENCE_FLIPPED"
    return "REFERENCE_ROBUST"


def classify_bounded_sensitivity(*, value: Decimal, target: Decimal,
                                 error_bound_bps: Decimal) -> str:
    """Symmetric hypothetical bound: robust or ambiguous, never claims an actual flip."""
    if any(not x.is_finite() for x in (value, target, error_bound_bps)) or target <= 0 or error_bound_bps < 0:
        raise ValueError("invalid sensitivity input")
    bound = target * error_bound_bps / Decimal("10000")
    return "REFERENCE_ROBUST" if abs(value - target) > bound else "REFERENCE_AMBIGUOUS"


def specification_manifest(specs: dict[str, SettlementEventSpec]) -> dict:
    return {"version": SETTLEMENT_SPEC_VERSION, "status": STATUS,
            "assets": {asset: asdict(spec) for asset, spec in specs.items()}}
