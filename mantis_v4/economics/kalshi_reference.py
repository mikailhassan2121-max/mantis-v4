"""Experimental Kalshi-native reference policy and fee model.

This module is deliberately separate from the historically frozen Phase 6
policy.  It estimates the Kalshi event using the exchange-supplied starting
target and a provenance-labelled current-value proxy.  Nothing here is wired
to live selection or order execution.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
from typing import Optional

import numpy as np
import pandas as pd

from mantis_v4.entry import DecisionPolicy
from mantis_v4.simulation import (
    combine_channels,
    compute_fragility,
    conservative_lower_bound,
    digital_sensitivities,
    gaussian_terminal,
    scaled_sensitivities,
    student_t_terminal,
    volatility_of_volatility,
)

UTC = timezone.utc

MODEL_POLICY = "KALSHI_REFERENCE_V1"
QUALIFICATION_POLICY = "KALSHI_H_V1"
GATE_STATUS = "TRANSFERRED_UNVALIDATED_GATES"
SELECTION_POLICY = "PRIMARY_SELECTOR_V1"

REFERENCE_SOURCE = "KALSHI_MARKET_TARGET"
SETTLEMENT_PROVENANCE = "KALSHI_CRYPTO15M_CF_BENCHMARKS"
CURRENT_VALUE_PROVENANCE = "YAHOO_1M_CLOSE_PROXY_FOR_CF_BENCHMARKS"
ENDPOINT_MODEL = "TERMINAL_PRICE_CONSERVATIVE_PROXY_NO_SQRT60"

KALSHI_FEE_MODEL_VERSION = "KALSHI_QUADRATIC_TAKER_V1"
KALSHI_FEE_PROVENANCE = "KALSHI_OFFICIAL_FEE_SCHEDULE_2026-07-07"
KALSHI_FEE_SOURCE = "https://kalshi.com/regulatory/fee-schedule"


TRANSFERRED_POLICY = DecisionPolicy(
    QUALIFICATION_POLICY,
    probability_threshold=.95,
    lcb_threshold=.90,
    max_fragility=50,
    max_disagreement=.05,
    max_seconds_remaining=300,
    max_crossing_probability=.35,
    max_crossings=4,
)


@dataclass(frozen=True)
class KalshiReferenceProbability:
    model_policy: str
    qualification_policy: str
    gate_status: str
    current_value: Decimal
    current_value_provenance: str
    target: Decimal
    reference_source: str
    settlement_reference_provenance: str
    endpoint_model: str
    event_definition: str
    p_yes: float
    conservative_bound: float
    disagreement: float
    crossing_probability: float
    buffer_fraction: float
    reference_gap_absolute: Optional[Decimal]
    reference_gap_bps: Optional[Decimal]


def kalshi_reference_probability(*, current_value: Decimal, target: Decimal,
                                 sigma_remaining: float,
                                 old_proxy_reference: Optional[Decimal] = None,
                                 current_value_provenance: str = CURRENT_VALUE_PROVENANCE
                                 ) -> KalshiReferenceProbability:
    """Estimate P(endpoint average >= actual Kalshi target).

    V1 intentionally retains terminal Normal-Z variance.  A 60-observation
    ``sqrt(60)`` reduction would assume independence despite second-level price
    autocorrelation and overlapping endpoint construction.  Retaining terminal
    variance is a conservative approximation away from the strike and remains
    explicitly experimental.
    """
    current_value, target = Decimal(current_value), Decimal(target)
    if not current_value.is_finite() or not target.is_finite() or current_value <= 0 or target <= 0:
        raise ValueError("current value and Kalshi target must be positive finite Decimals")
    if not np.isfinite(sigma_remaining) or sigma_remaining <= 0:
        raise ValueError("sigma_remaining must be positive and finite")
    buffer_fraction = float((current_value - target) / target)
    spot, reference = float(current_value), float(target)
    anchor = gaussian_terminal(np.array([buffer_fraction]), np.array([sigma_remaining]),
                               spot=np.array([spot]), reference=np.array([reference]))
    heavy = student_t_terminal(np.array([buffer_fraction]), np.array([sigma_remaining]),
                               nu=4.456245976114701, spot=np.array([spot]))
    channels = {"gaussian": anchor.p_yes, "student_t": heavy.p_yes}
    agreement = combine_channels(channels)
    lower = conservative_lower_bound(channels)
    gap_abs = gap_bps = None
    if old_proxy_reference is not None:
        old = Decimal(old_proxy_reference)
        if not old.is_finite() or old <= 0:
            raise ValueError("old proxy reference must be positive and finite")
        gap_abs = target - old
        gap_bps = (gap_abs / old) * Decimal("10000")
    return KalshiReferenceProbability(
        MODEL_POLICY, QUALIFICATION_POLICY, GATE_STATUS,
        current_value, current_value_provenance, target, REFERENCE_SOURCE,
        SETTLEMENT_PROVENANCE, ENDPOINT_MODEL,
        "KALSHI_ENDING_60S_CF_BENCHMARK_AVERAGE_GTE_STARTING_TARGET",
        float(anchor.p_yes[0]), float(lower[0]), float(agreement.disagreement[0]),
        float(anchor.p_cross_reference[0]), buffer_fraction, gap_abs, gap_bps,
    )


@dataclass(frozen=True)
class KalshiFeeQuote:
    contracts: Decimal
    price: Decimal
    fee: Decimal
    fee_type: str
    fee_multiplier: Decimal
    model_version: str = KALSHI_FEE_MODEL_VERSION
    provenance: str = KALSHI_FEE_PROVENANCE


class KalshiFeeModel:
    """Official immediately-matched (taker) quadratic transaction fee.

    Fee is calculated for the whole transaction and rounded upward to the next
    cent, not independently rounded per fractional contract.
    """
    RATE = Decimal("0.07")
    CENT = Decimal("0.01")

    @classmethod
    def taker_fee(cls, *, contracts: Decimal, price: Decimal,
                  fee_type: str, fee_multiplier: Decimal) -> KalshiFeeQuote:
        contracts, price, multiplier = Decimal(contracts), Decimal(price), Decimal(fee_multiplier)
        if fee_type != "quadratic":
            raise ValueError("FEE_UNVERIFIED: unsupported Kalshi series fee type")
        if not all(x.is_finite() for x in (contracts, price, multiplier)):
            raise ValueError("FEE_UNVERIFIED: non-finite fee input")
        if contracts <= 0 or not Decimal("0") < price < Decimal("1") or multiplier <= 0:
            raise ValueError("FEE_UNVERIFIED: invalid fee input")
        raw = cls.RATE * multiplier * contracts * price * (Decimal("1") - price)
        fee = raw.quantize(cls.CENT, rounding=ROUND_CEILING)
        return KalshiFeeQuote(contracts, price, fee, fee_type, multiplier)


@dataclass(frozen=True)
class HistoricalKalshiContract:
    asset: str
    series_ticker: str
    market_ticker: str
    event_ticker: str
    window_start_utc: datetime
    window_end_utc: datetime
    target: Decimal
    ending_benchmark: Optional[Decimal]
    outcome_yes: int
    result: str
    settlement_source: str = "CF_BENCHMARKS"
    settlement_reference_provenance: str = SETTLEMENT_PROVENANCE


def parse_historical_market(asset: str, series_ticker: str, row: dict) -> HistoricalKalshiContract:
    """Strictly parse one finalized public historical/live-tier market row."""
    if row.get("status") != "finalized" or row.get("result") not in {"yes", "no"}:
        raise ValueError("historical market is not finalized with a binary result")
    if row.get("strike_type") != "greater_or_equal":
        raise ValueError("historical market has incompatible strike semantics")
    start = datetime.fromisoformat(str(row["open_time"]).replace("Z", "+00:00")).astimezone(UTC)
    end = datetime.fromisoformat(str(row["close_time"]).replace("Z", "+00:00")).astimezone(UTC)
    if (end - start).total_seconds() != 900 or start.minute % 15 or end.minute % 15:
        raise ValueError("historical market is not an exact quarter-hour window")
    target = Decimal(str(row["floor_strike"]))
    ending = Decimal(str(row["expiration_value"])) if row.get("expiration_value") not in (None, "") else None
    if target <= 0 or not target.is_finite() or (ending is not None and (ending <= 0 or not ending.is_finite())):
        raise ValueError("invalid historical benchmark value")
    result = str(row["result"])
    if ending is not None and int(ending >= target) != int(result == "yes"):
        raise ValueError("reported result conflicts with benchmark comparison")
    return HistoricalKalshiContract(asset, series_ticker, str(row["ticker"]),
                                    str(row["event_ticker"]), start, end, target,
                                    ending, int(result == "yes"), result)


def chronological_three_way_split(contracts: list[HistoricalKalshiContract],
                                  development_fraction=.60,
                                  validation_fraction=.20):
    """Window-grouped chronological development/validation/sealed-holdout split."""
    if not contracts:
        return [], [], []
    if not 0 < development_fraction < 1 or not 0 < validation_fraction < 1 or development_fraction + validation_fraction >= 1:
        raise ValueError("invalid split fractions")
    windows = sorted({c.window_end_utc for c in contracts})
    n = len(windows)
    dev_cut = max(1, int(n * development_fraction))
    val_cut = max(dev_cut + 1, int(n * (development_fraction + validation_fraction)))
    val_cut = min(val_cut, n - 1) if n > 2 else n
    dev_windows, val_windows = set(windows[:dev_cut]), set(windows[dev_cut:val_cut])
    dev = [c for c in contracts if c.window_end_utc in dev_windows]
    val = [c for c in contracts if c.window_end_utc in val_windows]
    hold = [c for c in contracts if c.window_end_utc not in dev_windows | val_windows]
    return dev, val, hold


def fetch_settled_contracts(client, *, asset: str, series_ticker: str,
                            max_pages_per_tier: int = 5) -> list[HistoricalKalshiContract]:
    """Bounded public reconstruction across Kalshi's live and archive tiers."""
    if max_pages_per_tier < 1 or max_pages_per_tier > 20:
        raise ValueError("max_pages_per_tier must be between 1 and 20")
    rows: dict[str, HistoricalKalshiContract] = {}
    for path, params in (("/markets", {"series_ticker": series_ticker, "status": "settled", "limit": 1000}),
                         ("/historical/markets", {"series_ticker": series_ticker, "limit": 1000})):
        cursor = None
        for _ in range(max_pages_per_tier):
            query = dict(params)
            if cursor:
                query["cursor"] = cursor
            payload = client.get(path, **query)
            markets = payload.get("markets")
            if not isinstance(markets, list):
                raise ValueError("Kalshi historical response has no markets list")
            for row in markets:
                try:
                    parsed = parse_historical_market(asset, series_ticker, row)
                except ValueError:
                    continue
                rows[parsed.market_ticker] = parsed
            cursor = payload.get("cursor")
            if not cursor:
                break
    return sorted(rows.values(), key=lambda x: (x.window_end_utc, x.asset))


def build_kalshi_states(table: pd.DataFrame) -> pd.DataFrame:
    """Apply unchanged Normal-Z machinery to actual Kalshi targets.

    Input rows must already contain causal current values and target-relative
    crossing counts.  The function does not derive labels or inspect outcomes
    while computing probabilities.
    """
    required = {"contract_id", "asset", "group_key", "window_epoch", "seconds_remaining",
                "spot", "kalshi_target", "realized_vol_1m", "sigma_remaining_return",
                "rv_5m", "rv_30m", "crossings", "outcome_yes"}
    missing = required - set(table)
    if missing:
        raise ValueError(f"missing Kalshi state columns: {sorted(missing)}")
    spot = table.spot.to_numpy(dtype=float)
    target = table.kalshi_target.to_numpy(dtype=float)
    seconds = table.seconds_remaining.to_numpy(dtype=float)
    sigma1 = table.realized_vol_1m.to_numpy(dtype=float)
    sigma_remaining = table.sigma_remaining_return.to_numpy(dtype=float)
    buffer = (spot - target) / target
    anchor = gaussian_terminal(buffer, sigma_remaining, spot=spot, reference=target)
    heavy = student_t_terminal(buffer, sigma_remaining, nu=4.456245976114701, spot=spot)
    channels = {"gaussian": anchor.p_yes, "student_t": heavy.p_yes}
    agreement = combine_channels(channels)
    lower = conservative_lower_bound(channels)
    sens = digital_sensitivities(spot=spot, reference=target,
                                 seconds_remaining=seconds, sigma_1m=sigma1)
    scaled = scaled_sensitivities(sens, spot, sigma1)
    frag = compute_fragility(
        gamma_per_pct2=scaled["gamma_per_pct2"],
        vega_per_10pct_vol=scaled["vega_per_10pct_vol"],
        theta_per_30s=scaled["theta_per_30s"], abs_z=np.abs(sens.z),
        seconds_remaining=seconds, crossings=table.crossings.to_numpy(dtype=float),
        vol_of_vol=volatility_of_volatility(table.rv_5m.to_numpy(dtype=float),
                                            table.rv_30m.to_numpy(dtype=float)),
        disagreement=agreement.disagreement,
    )
    out = table[["contract_id", "asset", "group_key", "window_epoch",
                 "seconds_remaining", "crossings", "outcome_yes"]].copy()
    out["normal_z"] = sens.z
    out["p_yes"] = anchor.p_yes
    out["lower_bound"] = lower
    out["disagreement"] = agreement.disagreement
    out["fragility"] = frag.score
    out["crossing_risk"] = anchor.p_cross_reference
    out["reference_valid"] = np.isfinite(target) & (target > 0)
    out["sufficient_history"] = True
    out["data_fresh"] = True
    out["contract_valid"] = out.contract_id.astype(str).str.len().gt(0)
    return out
