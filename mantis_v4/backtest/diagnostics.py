"""
MANTIS V4 — dataset-quality and cross-asset diagnostics (Phase 3 req. 10 + extra).

Two jobs.

1.  DATA QUALITY. Missing candles, stale sequences, duplicate timestamps,
    timezone consistency, incomplete contracts, contracts with no start
    reference, contracts with no terminal bar, and the percentage of usable
    contracts per asset. Nothing is repaired — gaps are counted and reported,
    never filled (Phase 3 requirement 9).

2.  CROSS-ASSET CORRELATION. Added at your request, because Phase 2 observed
    all five assets settling YES in the same 15-minute window.

    That single observation is not evidence of anything on its own — under any
    plausible market it has a decent chance of happening by luck. What matters
    is whether it is SYSTEMATIC, because if it is, three modelling assumptions
    break at once:

      * treating each coin as an independent draw overstates effective sample
        size, so every confidence interval computed per-coin is too narrow;
      * "diversifying" across five coins in one window is not diversification —
        it is one position in five wrappers, and the tail risk is five times
        what per-coin sizing implies;
      * a walk-forward split that puts BTC and ETH rows from the SAME window on
        opposite sides of the boundary leaks, even though each asset's series is
        chronologically clean.

    So we measure three things: return correlation, outcome agreement versus
    what independence would predict, and the distribution of unanimity.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Optional, Sequence

import numpy as np
import pandas as pd

from .features import contiguous_returns


# ---------------------------------------------------------------------------
# Bar-level quality
# ---------------------------------------------------------------------------

@dataclass
class BarQualityReport:
    asset: str
    bars: int = 0
    span_days: float = 0.0
    expected_bars: int = 0
    missing_bars: int = 0
    completeness: float = 0.0
    duplicate_timestamps: int = 0
    out_of_order: bool = False
    timezone: str = ""
    timezone_consistent: bool = True
    gap_count: int = 0
    max_gap_minutes: float = 0.0
    longest_stale_run: int = 0
    stale_runs_over_5: int = 0
    nan_rows: int = 0
    nonpositive_rows: int = 0

    def as_row(self) -> dict:
        return {
            "asset": self.asset,
            "bars": self.bars,
            "span_days": round(self.span_days, 3),
            "expected_bars": self.expected_bars,
            "missing_bars": self.missing_bars,
            "completeness_pct": round(self.completeness * 100, 3),
            "duplicate_timestamps": self.duplicate_timestamps,
            "out_of_order": self.out_of_order,
            "timezone": self.timezone,
            "timezone_consistent": self.timezone_consistent,
            "gap_count": self.gap_count,
            "max_gap_minutes": round(self.max_gap_minutes, 1),
            "longest_stale_run": self.longest_stale_run,
            "stale_runs_over_5": self.stale_runs_over_5,
            "nan_rows": self.nan_rows,
            "nonpositive_rows": self.nonpositive_rows,
        }


def longest_identical_run(values: Sequence[float]) -> tuple[int, int]:
    """Longest run of identical consecutive closes, and how many runs exceed 5.

    A frozen or synthetic feed shows up here even when its timestamps keep
    advancing — the condition that let V3 report 96.8% confidence off a stalled
    feed (audit B-5). Timestamp freshness cannot detect it.
    """
    longest = 0
    over_five = 0
    current = 1
    for i in range(1, len(values)):
        if values[i] == values[i - 1]:
            current += 1
        else:
            if current > 5:
                over_five += 1
            longest = max(longest, current)
            current = 1
    if current > 5:
        over_five += 1
    return max(longest, current), over_five


def analyse_bars(asset: str, frame: pd.DataFrame) -> BarQualityReport:
    """Describe a bar series without changing it."""
    report = BarQualityReport(asset=asset)
    if frame is None or len(frame) == 0:
        return report

    index = pd.DatetimeIndex(frame.index)
    report.bars = len(frame)
    report.timezone = str(index.tz) if index.tz is not None else "NAIVE"
    report.timezone_consistent = index.tz is not None
    report.duplicate_timestamps = int(index.duplicated().sum())
    report.out_of_order = not index.is_monotonic_increasing

    span_seconds = (index[-1] - index[0]).total_seconds()
    report.span_days = span_seconds / 86400.0
    report.expected_bars = int(span_seconds // 60) + 1
    report.missing_bars = max(0, report.expected_bars - report.bars)
    report.completeness = (
        report.bars / report.expected_bars if report.expected_bars > 0 else 0.0
    )

    deltas = index.to_series().diff().dropna()
    if len(deltas):
        step = pd.Timedelta(minutes=1)
        report.gap_count = int((deltas > step).sum())
        report.max_gap_minutes = float(deltas.max().total_seconds() / 60.0)

    closes = frame["Close"].astype(float).to_numpy()
    report.longest_stale_run, report.stale_runs_over_5 = longest_identical_run(closes)

    ohlc = frame[["Open", "High", "Low", "Close"]].to_numpy(dtype="float64")
    report.nan_rows = int((~np.isfinite(ohlc)).any(axis=1).sum())
    report.nonpositive_rows = int((ohlc <= 0).any(axis=1).sum())

    return report


# ---------------------------------------------------------------------------
# Contract-level quality
# ---------------------------------------------------------------------------

@dataclass
class ContractQualityReport:
    asset: str
    total_contracts: int = 0
    usable_contracts: int = 0
    no_start_reference: int = 0
    no_terminal_bar: int = 0
    unsettleable: int = 0
    no_data: int = 0
    yes_outcomes: int = 0
    no_outcomes: int = 0

    @property
    def usable_pct(self) -> float:
        if self.total_contracts <= 0:
            return 0.0
        return self.usable_contracts / self.total_contracts * 100.0

    @property
    def base_rate_yes(self) -> Optional[float]:
        if self.usable_contracts <= 0:
            return None
        return self.yes_outcomes / self.usable_contracts

    def as_row(self) -> dict:
        return {
            "asset": self.asset,
            "total_contracts": self.total_contracts,
            "usable_contracts": self.usable_contracts,
            "usable_pct": round(self.usable_pct, 2),
            "no_start_reference": self.no_start_reference,
            "no_terminal_bar": self.no_terminal_bar,
            "unsettleable": self.unsettleable,
            "no_data": self.no_data,
            "yes_outcomes": self.yes_outcomes,
            "no_outcomes": self.no_outcomes,
            "base_rate_yes": (
                None if self.base_rate_yes is None else round(self.base_rate_yes, 4)
            ),
        }


def analyse_contracts(replays: Sequence) -> dict[str, ContractQualityReport]:
    reports: dict[str, ContractQualityReport] = {}
    for replay in replays:
        report = reports.setdefault(replay.asset, ContractQualityReport(asset=replay.asset))
        report.total_contracts += 1

        status = replay.data_quality_status
        if status == "NO_START_REFERENCE":
            report.no_start_reference += 1
        elif status == "NO_TERMINAL_BAR":
            report.no_terminal_bar += 1
        elif status == "UNSETTLEABLE":
            report.unsettleable += 1
        elif status == "NO_DATA":
            report.no_data += 1

        if replay.usable:
            report.usable_contracts += 1
            if replay.outcome_yes:
                report.yes_outcomes += 1
            else:
                report.no_outcomes += 1

    return reports


# ---------------------------------------------------------------------------
# Cross-asset correlation
# ---------------------------------------------------------------------------

@dataclass
class CrossAssetReport:
    """Evidence about whether the coins move and settle together."""

    assets: list[str] = field(default_factory=list)
    return_correlation: dict[str, dict[str, float]] = field(default_factory=dict)
    mean_return_correlation: Optional[float] = None

    outcome_agreement: dict[str, dict[str, float]] = field(default_factory=dict)
    mean_outcome_agreement: Optional[float] = None

    shared_windows: int = 0
    unanimous_windows: int = 0
    unanimous_rate: Optional[float] = None
    expected_unanimous_rate_if_independent: Optional[float] = None
    unanimity_histogram: dict[int, int] = field(default_factory=dict)

    effective_sample_size_factor: Optional[float] = None
    base_rate_yes: Optional[float] = None

    def as_dict(self) -> dict:
        return {
            "assets": self.assets,
            "mean_return_correlation": self.mean_return_correlation,
            "mean_outcome_agreement": self.mean_outcome_agreement,
            "shared_windows": self.shared_windows,
            "unanimous_windows": self.unanimous_windows,
            "unanimous_rate": self.unanimous_rate,
            "expected_unanimous_rate_if_independent":
                self.expected_unanimous_rate_if_independent,
            "unanimity_histogram": self.unanimity_histogram,
            "effective_sample_size_factor": self.effective_sample_size_factor,
            "base_rate_yes": self.base_rate_yes,
        }


def return_correlations(frames: dict[str, pd.DataFrame]) -> tuple[dict, Optional[float]]:
    """Pearson correlation of contiguous 1-minute log returns, pairwise."""
    series = {}
    for asset, frame in frames.items():
        rets = contiguous_returns(frame)
        if len(rets) >= 100:
            series[asset] = rets

    assets = sorted(series)
    matrix: dict[str, dict[str, float]] = {a: {} for a in assets}
    off_diagonal: list[float] = []

    for i, a in enumerate(assets):
        for j, b in enumerate(assets):
            if i == j:
                matrix[a][b] = 1.0
                continue
            joined = pd.concat([series[a], series[b]], axis=1, join="inner").dropna()
            if len(joined) < 100:
                matrix[a][b] = float("nan")
                continue
            corr = float(joined.iloc[:, 0].corr(joined.iloc[:, 1]))
            matrix[a][b] = corr
            if i < j and np.isfinite(corr):
                off_diagonal.append(corr)

    mean_corr = sum(off_diagonal) / len(off_diagonal) if off_diagonal else None
    return matrix, mean_corr


def analyse_cross_asset(
    frames: dict[str, pd.DataFrame],
    replays: Sequence,
) -> CrossAssetReport:
    """Quantify co-movement in returns AND in settled outcomes."""
    report = CrossAssetReport()

    report.return_correlation, report.mean_return_correlation = return_correlations(frames)

    # Outcomes keyed by window, so only genuinely simultaneous contracts compare.
    by_window: dict[str, dict[str, bool]] = defaultdict(dict)
    for replay in replays:
        if replay.usable and replay.outcome_yes is not None:
            by_window[replay.window.contract_id][replay.asset] = bool(replay.outcome_yes)

    assets = sorted({a for outcomes in by_window.values() for a in outcomes})
    report.assets = assets
    if not assets:
        return report

    # Pairwise agreement rate.
    agreement: dict[str, dict[str, float]] = {a: {} for a in assets}
    pair_values: list[float] = []
    for i, a in enumerate(assets):
        for j, b in enumerate(assets):
            if i == j:
                agreement[a][b] = 1.0
                continue
            both = [
                (o[a], o[b]) for o in by_window.values() if a in o and b in o
            ]
            if len(both) < 20:
                agreement[a][b] = float("nan")
                continue
            rate = sum(1 for x, y in both if x == y) / len(both)
            agreement[a][b] = rate
            if i < j:
                pair_values.append(rate)
    report.outcome_agreement = agreement
    report.mean_outcome_agreement = (
        sum(pair_values) / len(pair_values) if pair_values else None
    )

    # Unanimity across the full asset set.
    full = [o for o in by_window.values() if len(o) == len(assets)]
    report.shared_windows = len(full)

    total_yes = 0
    total_contracts = 0
    histogram: Counter = Counter()
    unanimous = 0

    for outcomes in full:
        yes_count = sum(1 for v in outcomes.values() if v)
        histogram[yes_count] += 1
        total_yes += yes_count
        total_contracts += len(outcomes)
        if yes_count in (0, len(outcomes)):
            unanimous += 1

    report.unanimity_histogram = dict(sorted(histogram.items()))
    report.unanimous_windows = unanimous

    if full:
        report.unanimous_rate = unanimous / len(full)
        base = total_yes / total_contracts if total_contracts else 0.5
        report.base_rate_yes = base
        n = len(assets)
        # If assets were independent Bernoulli(base), P(all YES) + P(all NO).
        report.expected_unanimous_rate_if_independent = base**n + (1 - base) ** n

        # A crude but honest effective-sample-size deflator: with mean pairwise
        # outcome agreement r among n assets, the number of INDEPENDENT
        # observations per window is roughly n / (1 + (n-1) * rho), where rho is
        # the excess agreement above chance rescaled to [0, 1].
        if report.mean_outcome_agreement is not None:
            chance = base**2 + (1 - base) ** 2
            excess = max(0.0, (report.mean_outcome_agreement - chance) / max(1e-9, 1 - chance))
            report.effective_sample_size_factor = n / (1 + (n - 1) * excess)

    return report
