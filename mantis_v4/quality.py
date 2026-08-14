"""
MANTIS V4 — data-quality gate.

Master prompt section 18. Before any trade:
    latest price age below threshold, no duplicate/out-of-order candles,
    enough history, no missing required feature, valid current contract ID,
    correct timezone, fresh contract quote if EV mode is active, reference
    known, no contract rollover race, no NaN/inf, cross-provider sanity check
    if configured.

If data quality fails:  DATA HOLD -- DO NOT ENTER.

Audit findings addressed:

  B-5  a frozen feed collapses realised volatility toward the floor and
       manufactures near-maximum confidence. V3's only staleness test read the
       last bar's TIMESTAMP, which a stalled feed keeps advancing. The
       ``price_movement`` check reads the PRICES.
  D-2  V3 permitted a 5-minute-old price to drive a 15-minute contract -- 33%
       of the contract's life -- and displayed it with no age indication.
  D-6  V3's ``data_age_minutes`` returned 0.0 (maximum freshness) on any
       unexpected exception. Every check here fails CLOSED.
  A-4  the rollover-race check verifies that the bars, the contract window and
       the scan instant all describe the same window.

Design rule: a check that cannot be evaluated is a FAILURE, never a pass. The
whole point of this module is to be the thing that says no.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd

from .clock import Instant
from .contracts import ContractSpec, ContractWindow
from .providers.base import BarSet
from .providers.market_data import (
    bar_gap_report,
    contiguous_log_returns,
    distinct_close_count,
)


class CheckStatus(Enum):
    PASS = "PASS"
    WARN = "WARN"     # usable, but recorded and displayed
    FAIL = "FAIL"     # blocks entry


@dataclass
class QualityCheck:
    name: str
    status: CheckStatus
    detail: str = ""

    @property
    def blocking(self) -> bool:
        return self.status is CheckStatus.FAIL


@dataclass
class DataQualityReport:
    """Result of every gate for one asset in one scan."""

    asset: str
    checks: list[QualityCheck] = field(default_factory=list)

    def add(self, name: str, status: CheckStatus, detail: str = "") -> None:
        self.checks.append(QualityCheck(name=name, status=status, detail=detail))

    @property
    def passed(self) -> bool:
        """True only when nothing is blocking. Absence of checks is NOT a pass."""
        return bool(self.checks) and not any(c.blocking for c in self.checks)

    @property
    def failures(self) -> list[QualityCheck]:
        return [c for c in self.checks if c.status is CheckStatus.FAIL]

    @property
    def warnings(self) -> list[QualityCheck]:
        return [c for c in self.checks if c.status is CheckStatus.WARN]

    @property
    def summary(self) -> str:
        if self.passed:
            warn = len(self.warnings)
            return "OK" if not warn else f"OK ({warn} warning{'s' if warn != 1 else ''})"
        first = self.failures[0]
        extra = len(self.failures) - 1
        text = f"DATA HOLD - {first.name}: {first.detail}" if first.detail else f"DATA HOLD - {first.name}"
        return text if extra <= 0 else f"{text} (+{extra} more)"

    def as_dict(self) -> dict:
        return {
            "asset": self.asset,
            "passed": self.passed,
            "failures": [f"{c.name}: {c.detail}" for c in self.failures],
            "warnings": [f"{c.name}: {c.detail}" for c in self.warnings],
        }


@dataclass
class QualityThresholds:
    """Every knob the gate uses. Mirrors MantisConfig; no hidden constants."""

    max_data_age_seconds: float = 90.0
    max_quote_age_seconds: float = 15.0
    min_candles: int = 60
    stale_price_min_distinct_closes: int = 5
    stale_price_lookback_bars: int = 20
    min_realized_vol_1m: float = 1e-6
    max_gap_minutes: float = 5.0
    cross_provider_tolerance_pct: float = 0.01   # 1%


def evaluate_data_quality(
    *,
    asset: str,
    bars: Optional[BarSet],
    window: ContractWindow,
    instant: Instant,
    spec: Optional[ContractSpec],
    thresholds: QualityThresholds,
    require_economics: bool = False,
    cross_provider_price: Optional[float] = None,
) -> DataQualityReport:
    """Run every section-18 gate. Returns a report; NEVER raises.

    The outer guard is the fail-closed contract: if this function hits an
    unexpected condition, the correct outcome is DATA HOLD, not a traceback and
    not an optimistic default. Audit D-6 found V3's data-age helper doing the
    opposite -- reporting maximum freshness on any exception.
    """
    try:
        return _evaluate(
            asset=asset,
            bars=bars,
            window=window,
            instant=instant,
            spec=spec,
            thresholds=thresholds,
            require_economics=require_economics,
            cross_provider_price=cross_provider_price,
        )
    except Exception as exc:  # noqa: BLE001 - fail closed, always
        report = DataQualityReport(asset=asset)
        report.add(
            "gate_internal_error",
            CheckStatus.FAIL,
            f"{type(exc).__name__}: {exc}",
        )
        return report


def _evaluate(
    *,
    asset: str,
    bars: Optional[BarSet],
    window: ContractWindow,
    instant: Instant,
    spec: Optional[ContractSpec],
    thresholds: QualityThresholds,
    require_economics: bool = False,
    cross_provider_price: Optional[float] = None,
) -> DataQualityReport:
    report = DataQualityReport(asset=asset)

    # -- 1. bars exist at all ------------------------------------------------
    if bars is None or bars.is_empty:
        report.add("bars_present", CheckStatus.FAIL, "no market data")
        return report
    report.add("bars_present", CheckStatus.PASS, f"{len(bars)} bars")

    frame: pd.DataFrame = bars.frame

    # -- 2. enough history ---------------------------------------------------
    if len(frame) < thresholds.min_candles:
        report.add(
            "sufficient_history",
            CheckStatus.FAIL,
            f"{len(frame)}/{thresholds.min_candles} bars",
        )
    else:
        report.add("sufficient_history", CheckStatus.PASS, f"{len(frame)} bars")

    # -- 3. timezone correctness --------------------------------------------
    # This check gates everything below it. A tz-naive index cannot be compared
    # against the scan instant at all, so we must stop here rather than let a
    # later check raise -- the gate's job is to return a verdict, never to throw.
    index = pd.DatetimeIndex(frame.index)
    if index.tz is None:
        report.add("timezone_aware", CheckStatus.FAIL, "bar index is tz-naive")
        return report
    report.add("timezone_aware", CheckStatus.PASS, str(index.tz))

    # -- 4. ordering and duplicates -----------------------------------------
    # Also gating: gap and volatility maths below assume a sorted, unique index.
    if not index.is_monotonic_increasing:
        report.add("bar_ordering", CheckStatus.FAIL, "bars out of order")
        return report
    if index.has_duplicates:
        report.add("bar_ordering", CheckStatus.FAIL, "duplicate bar timestamps")
        return report
    report.add("bar_ordering", CheckStatus.PASS, "monotonic, unique")

    # -- 5. data age (audit D-2, D-6: fails closed) --------------------------
    last_start = index[-1].to_pydatetime()
    age_seconds = (instant.utc - last_start).total_seconds()
    if age_seconds < 0:
        # A bar timestamped in the future means a clock or timezone problem.
        report.add(
            "data_age",
            CheckStatus.FAIL,
            f"last bar is {abs(age_seconds):.0f}s in the future",
        )
    elif age_seconds > thresholds.max_data_age_seconds:
        report.add(
            "data_age",
            CheckStatus.FAIL,
            f"{age_seconds:.0f}s old (limit {thresholds.max_data_age_seconds:.0f}s)",
        )
    else:
        report.add("data_age", CheckStatus.PASS, f"{age_seconds:.0f}s old")

    # -- 6. NaN / inf in recent bars ----------------------------------------
    recent = frame.tail(max(thresholds.stale_price_lookback_bars, 5))
    numeric = recent[["Open", "High", "Low", "Close"]].to_numpy(dtype="float64")
    if not np.isfinite(numeric).all():
        report.add("finite_values", CheckStatus.FAIL, "NaN/inf in recent OHLC")
    elif (numeric <= 0).any():
        report.add("finite_values", CheckStatus.FAIL, "non-positive price in recent OHLC")
    else:
        report.add("finite_values", CheckStatus.PASS, "finite, positive")

    # -- 7. price actually moving (audit B-5) -------------------------------
    distinct = distinct_close_count(frame, thresholds.stale_price_lookback_bars)
    if distinct < thresholds.stale_price_min_distinct_closes:
        report.add(
            "price_movement",
            CheckStatus.FAIL,
            f"only {distinct} distinct closes in last "
            f"{thresholds.stale_price_lookback_bars} bars - feed may be frozen",
        )
    else:
        report.add("price_movement", CheckStatus.PASS, f"{distinct} distinct closes")

    # -- 8. volatility is estimable and non-degenerate ----------------------
    returns = contiguous_log_returns(frame)
    if len(returns) < 10:
        report.add(
            "volatility_estimable",
            CheckStatus.FAIL,
            f"only {len(returns)} contiguous 1m returns",
        )
    else:
        realized = float(returns.tail(20).std(ddof=1))
        if not np.isfinite(realized) or realized < thresholds.min_realized_vol_1m:
            report.add(
                "volatility_estimable",
                CheckStatus.FAIL,
                f"degenerate realised vol {realized:.3e}",
            )
        else:
            report.add("volatility_estimable", CheckStatus.PASS, f"sigma_1m={realized:.3e}")

    # -- 9. bar gaps (audit D-5) --------------------------------------------
    gaps = bar_gap_report(frame)
    if gaps["max_gap_minutes"] > thresholds.max_gap_minutes:
        report.add(
            "bar_continuity",
            CheckStatus.WARN,
            f"{gaps['gaps']} gaps, largest {gaps['max_gap_minutes']:.0f}m",
        )
    else:
        report.add("bar_continuity", CheckStatus.PASS, f"{gaps['gaps']} gaps")

    # -- 10. partial in-progress bar (audit D-4) ----------------------------
    if bars.last_bar_is_partial:
        report.add(
            "partial_bar",
            CheckStatus.WARN,
            "last bar still forming; High/Low/Volume incomplete",
        )
    else:
        report.add("partial_bar", CheckStatus.PASS, "last bar complete")

    # -- 11. serving from cache ---------------------------------------------
    if bars.from_cache:
        report.add("live_feed", CheckStatus.WARN, "serving cached bars")
    else:
        report.add("live_feed", CheckStatus.PASS, bars.source)

    # -- 12. contract rollover race (audit A-4) -----------------------------
    if not window.contains(instant):
        report.add(
            "contract_window_current",
            CheckStatus.FAIL,
            "scan instant is outside the window being evaluated",
        )
    else:
        report.add("contract_window_current", CheckStatus.PASS, window.contract_id)

    # -- 13. reference is known ---------------------------------------------
    if spec is None or not spec.has_reference:
        report.add("reference_known", CheckStatus.FAIL, "no settlement reference")
    elif not spec.reference_is_verified:
        report.add(
            "reference_known",
            CheckStatus.WARN,
            f"{spec.reference_source.display} - research only",
        )
    else:
        report.add("reference_known", CheckStatus.PASS, spec.reference_source.display)

    # -- 14. quote freshness, only when EV mode is active -------------------
    if require_economics:
        if spec is None:
            report.add("quote_fresh", CheckStatus.FAIL, "no contract spec")
        elif not spec.quote.is_two_sided:
            report.add("quote_fresh", CheckStatus.FAIL, "quote not two-sided")
        elif not spec.quote.is_sane():
            report.add("quote_fresh", CheckStatus.FAIL, "quote failed sanity check")
        elif not spec.quote_is_fresh(instant, thresholds.max_quote_age_seconds):
            age = spec.quote.age_seconds(instant)
            age_text = "unknown" if age is None else f"{age:.0f}s"
            report.add("quote_fresh", CheckStatus.FAIL, f"quote age {age_text}")
        else:
            report.add("quote_fresh", CheckStatus.PASS, "fresh two-sided quote")

    # -- 15. cross-provider sanity, only when configured --------------------
    if cross_provider_price is not None:
        last_close = bars.last_close
        if last_close is None or last_close <= 0:
            report.add("cross_provider", CheckStatus.FAIL, "no comparable price")
        else:
            divergence = abs(cross_provider_price - last_close) / last_close
            if divergence > thresholds.cross_provider_tolerance_pct:
                report.add(
                    "cross_provider",
                    CheckStatus.FAIL,
                    f"providers disagree by {divergence * 100:.2f}%",
                )
            else:
                report.add(
                    "cross_provider",
                    CheckStatus.PASS,
                    f"agree within {divergence * 100:.3f}%",
                )

    return report
