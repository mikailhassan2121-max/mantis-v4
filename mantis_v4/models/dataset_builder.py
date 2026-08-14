"""
MANTIS V4 — Phase 4 modelling dataset builder.

WHY THIS IS NOT THE PHASE 3 DATASET

Phase 3 produced one row per (contract, asset, strategy) at the moment a
strategy chose to enter. That is the right shape for comparing decision RULES
and the wrong shape for fitting a probability MODEL, because the rows are
selected by the rule being tested. Fitting on them would train the model only
on states some other strategy liked.

Phase 4 instead samples a FIXED grid of scan points inside every contract,
regardless of any strategy's opinion, and labels each with the contract's
terminal outcome. The model therefore learns P(YES | state at time t) across
the whole state space, not just the part V3 or Normal-Z happened to visit.

GROUPING IS THE CENTRAL CORRECTNESS CONCERN

Rows are correlated along two axes at once:

  * WITHIN a contract, the 15 scan points share one label. They are close to
    15 copies of one observation, not 15 observations.
  * ACROSS assets, Phase 3 measured 0.641 return correlation and only 1.72
    effective independent assets of five. BTC and ETH contracts for the same
    window are largely the same bet.

``group_key`` is therefore the CONTRACT WINDOW (not the asset, not the row), so
every scan of every asset for a given 15-minute window moves through the
splitter as a single unit. Any split that breaks that grouping leaks, and
``test_same_window_assets_never_split`` enforces it.

LEAKAGE

Features come from ``HistoricalMarketView`` (Phase 3's barrier) plus causal
precomputed indicator frames. The label comes from the terminal price, which is
read only after the whole grid is built. Nothing in the feature path can see it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable, Optional, Sequence
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from ..backtest.features import normal_cdf, precompute_indicators
from ..backtest.view import HistoricalMarketView, derive_proxy_reference
from ..clock import UTC
from ..contracts import ContractWindow, SettlementRule
from ..resolution import find_terminal_price
from .features_extended import (
    CROSS_ASSET_COLUMNS,
    EXTENDED_COLUMNS,
    PATH_COLUMNS,
    build_cross_asset_panel,
    precompute_extended,
    reference_path_features,
)

# Scan grid, in seconds remaining. Chosen to populate every entry-time bucket
# Phase 4 section 7 asks for: 600-900, 450-600, 300-450, 150-300, 60-150,
# 30-60, 0-30.
DEFAULT_SCAN_GRID = (840, 780, 720, 660, 600, 540, 480, 420, 360, 300, 240, 180, 120, 90, 60, 30)

# ---------------------------------------------------------------------------
# Feature families (Phase 4 section 3 + section 4 ablation)
# ---------------------------------------------------------------------------

GEOMETRY_FEATURES = [
    "spot", "reference", "buffer_abs", "buffer_pct", "buffer_bps",
    "seconds_remaining", "elapsed_fraction",
    "sigma_remaining_return", "buffer_over_sigma", "normal_z",
]

MOMENTUM_FEATURES = [
    "mom1", "mom2", "mom3", "mom5", "mom8", "mom10",
    "mom_norm_1", "mom_norm_3", "mom_norm_5",
    "momentum_acceleration", "trend_persistence",
]

VOLATILITY_FEATURES = [
    "rv_5m", "rv_15m", "rv_30m", "rv_ewma",
    "vol_ratio_short_long", "range_5m", "range_15m", "range_expansion",
    "atr_ratio", "atr_pct",
]

STRUCTURE_FEATURES = [
    "ema_stack_score", "ema_slope_5", "ema_slope_21",
    "ema5_minus_ema21_norm",
    "macd_hist_norm", "macd_hist_change_norm", "rsi",
    "body_pct", "close_location",
    "dist_from_high_20", "dist_from_low_20",
    "dist_from_high_60", "dist_from_low_60",
    "frac_above_reference", "crossings", "seconds_since_crossing",
    "crossing_intensity", "max_favourable_buffer", "max_adverse_buffer",
    "buffer_velocity",
]

CROSS_ASSET_FEATURES = [
    "xa_btc_mom5", "xa_market_mom5", "xa_dispersion",
    "xa_breadth", "xa_common_direction", "xa_rel_strength",
]

TIME_FEATURES = [
    "minute_in_contract", "hour_of_day_sin", "hour_of_day_cos", "is_weekend",
]

FEATURE_FAMILIES: dict[str, list[str]] = {
    "geometry": GEOMETRY_FEATURES,
    "momentum": MOMENTUM_FEATURES,
    "volatility": VOLATILITY_FEATURES,
    "structure": STRUCTURE_FEATURES,
    "cross_asset": CROSS_ASSET_FEATURES,
    "time": TIME_FEATURES,
}

ALL_FEATURES = [f for family in FEATURE_FAMILIES.values() for f in family]

META_COLUMNS = [
    "contract_id", "asset", "window_start_utc", "window_end_utc",
    "scan_utc", "group_key", "window_epoch",
    "terminal_price", "outcome_yes",
    "reference_verified", "economics_available", "reference_source",
]


@dataclass
class BuildReport:
    contracts_seen: int = 0
    contracts_used: int = 0
    contracts_skipped_no_reference: int = 0
    contracts_skipped_no_terminal: int = 0
    rows: int = 0
    rows_dropped_incomplete: int = 0
    assets: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"contracts {self.contracts_used}/{self.contracts_seen} used, "
            f"{self.rows} rows, {self.rows_dropped_incomplete} dropped incomplete"
        )


class ModellingDatasetBuilder:
    """Builds the Phase 4 feature/label table."""

    def __init__(
        self,
        frames: dict[str, pd.DataFrame],
        timezone: ZoneInfo,
        *,
        window_minutes: int = 15,
        scan_grid: Sequence[int] = DEFAULT_SCAN_GRID,
        warmup_bars: int = 120,
        min_bars_for_features: int = 60,
        settlement_rule: SettlementRule = SettlementRule.PROXY_TERMINAL_ABOVE_REFERENCE,
    ) -> None:
        self.frames = frames
        self.timezone = timezone
        self.window_minutes = window_minutes
        self.scan_grid = tuple(sorted(scan_grid, reverse=True))
        self.warmup_bars = warmup_bars
        self.min_bars_for_features = min_bars_for_features
        self.settlement_rule = settlement_rule

        self._base: dict[str, pd.DataFrame] = {}
        self._ext: dict[str, pd.DataFrame] = {}
        self._panel: Optional[pd.DataFrame] = None

    # ------------------------------------------------------------------

    def _prepare(self) -> None:
        if self._base:
            return
        for asset, frame in self.frames.items():
            self._base[asset] = precompute_indicators(frame)
            self._ext[asset] = precompute_extended(frame)
        self._panel = build_cross_asset_panel(self.frames)

    def windows_for(self, asset: str) -> list[ContractWindow]:
        """Same enumeration rule as the Phase 3 backtester."""
        frame = self.frames.get(asset)
        if frame is None or len(frame) <= self.warmup_bars:
            return []
        usable = frame.iloc[self.warmup_bars :]
        if usable.empty:
            return []

        first = usable.index[0].to_pydatetime()
        last = frame.index[-1].to_pydatetime()
        window = ContractWindow.for_instant(first, self.timezone, self.window_minutes)
        if window.start_utc < first:
            window = window.next_window()

        windows = []
        while window.end_utc <= last:
            windows.append(window)
            window = window.next_window()
        return windows

    # ------------------------------------------------------------------

    def build(self, assets: Optional[Sequence[str]] = None) -> tuple[pd.DataFrame, BuildReport]:
        self._prepare()
        report = BuildReport(assets=sorted(assets or self.frames))
        rows: list[dict] = []

        for asset in report.assets:
            frame = self.frames.get(asset)
            if frame is None:
                continue

            base = self._base[asset]
            ext = self._ext[asset]

            # Positional numpy views for the hot loop.
            index_values = frame.index.values
            highs = frame["High"].to_numpy(dtype="float64")
            lows = frame["Low"].to_numpy(dtype="float64")
            closes = frame["Close"].to_numpy(dtype="float64")
            bar_epochs = frame.index.view("int64") / 1e9

            for window in self.windows_for(asset):
                report.contracts_seen += 1

                reference = derive_proxy_reference(frame, window)
                if reference is None:
                    report.contracts_skipped_no_reference += 1
                    continue

                observation = find_terminal_price(frame, window.end_utc)
                if observation is None:
                    report.contracts_skipped_no_terminal += 1
                    continue

                outcome = self.settlement_rule.settle(observation.price, reference.price)
                if outcome is None:
                    report.contracts_skipped_no_terminal += 1
                    continue

                report.contracts_used += 1
                window_start_pos = int(
                    np.searchsorted(index_values, np.datetime64(window.start_utc.replace(tzinfo=None)), side="left")
                )

                for seconds_remaining in self.scan_grid:
                    scan_utc = window.end_utc - timedelta(seconds=seconds_remaining)
                    if scan_utc < window.start_utc:
                        continue

                    row = self._build_row(
                        asset=asset,
                        window=window,
                        scan_utc=scan_utc,
                        reference=reference,
                        outcome=outcome,
                        terminal_price=observation.price,
                        frame=frame,
                        base=base,
                        ext=ext,
                        highs=highs,
                        lows=lows,
                        closes=closes,
                        bar_epochs=bar_epochs,
                        window_start_pos=window_start_pos,
                    )
                    if row is None:
                        report.rows_dropped_incomplete += 1
                        continue
                    rows.append(row)

        table = pd.DataFrame(rows)
        report.rows = len(table)
        return table, report

    # ------------------------------------------------------------------

    def _build_row(
        self, *, asset, window, scan_utc, reference, outcome, terminal_price,
        frame, base, ext, highs, lows, closes, bar_epochs, window_start_pos,
    ) -> Optional[dict]:
        view = HistoricalMarketView(frame, scan_utc, asset=asset)
        if view.is_empty or len(view) < self.min_bars_for_features:
            return None
        if not reference.available_at(scan_utc):
            return None

        pos = len(view) - 1          # positional index of the last visible bar
        timestamp = frame.index[pos]

        try:
            b = base.iloc[pos]
            e = ext.iloc[pos]
        except (IndexError, KeyError):
            return None

        spot = float(b["close"])
        if not np.isfinite(spot) or spot <= 0:
            return None

        ref_price = reference.price
        buffer_abs = spot - ref_price
        buffer_pct = buffer_abs / ref_price

        seconds_remaining = window.seconds_remaining(scan_utc)
        minutes_left = seconds_remaining / 60.0
        elapsed_fraction = window.elapsed_fraction(scan_utc)

        realized_vol = float(b["realized_vol_1m"])
        if not np.isfinite(realized_vol) or realized_vol <= 0:
            return None

        sigma_remaining = realized_vol * np.sqrt(max(minutes_left, 0.0))
        if not np.isfinite(sigma_remaining) or sigma_remaining <= 0:
            return None

        buffer_over_sigma = buffer_pct / sigma_remaining

        row: dict = {
            "contract_id": window.contract_id,
            "asset": asset,
            "window_start_utc": window.start_utc,
            "window_end_utc": window.end_utc,
            "scan_utc": scan_utc,
            # Grouping unit: the WINDOW, so every asset's rows for one
            # 15-minute window move together through any splitter.
            "group_key": window.contract_id,
            "window_epoch": window.start_utc.timestamp(),
            "terminal_price": terminal_price,
            "outcome_yes": int(bool(outcome)),
            # Phase 3 requirement 1 carries forward unchanged.
            "reference_verified": 0,
            "economics_available": 0,
            "reference_source": "PROXY_WINDOW_OPEN",
        }

        # -- geometry -------------------------------------------------------
        row.update({
            "spot": spot,
            "reference": ref_price,
            "buffer_abs": buffer_abs,
            "buffer_pct": buffer_pct,
            "buffer_bps": buffer_pct * 10_000.0,
            "seconds_remaining": seconds_remaining,
            "elapsed_fraction": elapsed_fraction,
            # Retain the causal per-minute volatility input for Phase 5
            # terminal-distribution, simulation, and sensitivity diagnostics.
            # It is not promoted into ALL_FEATURES or fitted as a new predictor.
            "realized_vol_1m": realized_vol,
            "sigma_remaining_return": sigma_remaining,
            "buffer_over_sigma": buffer_over_sigma,
            "normal_z": buffer_over_sigma,
        })

        # -- momentum -------------------------------------------------------
        for name in ("mom1", "mom3", "mom5", "mom10", "momentum_acceleration"):
            row[name] = float(b[name])
        for name in ("mom2", "mom8", "mom_norm_1", "mom_norm_3", "mom_norm_5",
                     "trend_persistence"):
            row[name] = float(e[name])

        # -- volatility -----------------------------------------------------
        for name in ("rv_5m", "rv_15m", "rv_30m", "rv_ewma",
                     "vol_ratio_short_long", "range_5m", "range_15m",
                     "range_expansion", "atr_ratio"):
            row[name] = float(e[name])
        row["atr_pct"] = float(b["atr"]) / spot

        # -- structure ------------------------------------------------------
        for name in ("ema_stack_score", "ema_slope_5", "ema_slope_21",
                     "dist_from_high_20", "dist_from_low_20",
                     "dist_from_high_60", "dist_from_low_60"):
            row[name] = float(e[name])

        # Price-normalised so BTC (~63,000) and ADA (~0.18) are comparable.
        row["ema5_minus_ema21_norm"] = float(b["ema5_minus_ema21"]) / spot
        row["macd_hist_norm"] = float(b["macd_hist"]) / spot
        row["macd_hist_change_norm"] = float(b["macd_hist_change"]) / spot
        row["rsi"] = float(b["rsi"])
        row["body_pct"] = float(b["body_pct"])
        row["close_location"] = float(b["close_location"])

        # -- reference path (within-window) ---------------------------------
        path = reference_path_features(
            highs[window_start_pos : pos + 1],
            lows[window_start_pos : pos + 1],
            closes[window_start_pos : pos + 1],
            bar_epochs[window_start_pos : pos + 1],
            ref_price,
            scan_utc.timestamp(),
        )
        for name in PATH_COLUMNS:
            if name in STRUCTURE_FEATURES:
                row[name] = path[name]

        # -- cross-asset ----------------------------------------------------
        # Read at the SAME timestamp cutoff as the primary asset, so the
        # completed-bars-only rule applies identically to other assets.
        if self._panel is not None:
            try:
                p = self._panel.loc[timestamp]
                row["xa_btc_mom5"] = float(p["xa_btc_mom5"])
                row["xa_market_mom5"] = float(p["xa_market_mom5"])
                row["xa_dispersion"] = float(p["xa_dispersion"])
                row["xa_breadth"] = float(p["xa_breadth"])
                row["xa_common_direction"] = float(p["xa_common_direction"])
                rel = f"{asset}__rel_strength"
                row["xa_rel_strength"] = float(p[rel]) if rel in p.index else np.nan
            except KeyError:
                for name in CROSS_ASSET_FEATURES:
                    row[name] = np.nan
        else:
            for name in CROSS_ASSET_FEATURES:
                row[name] = np.nan

        # -- time -----------------------------------------------------------
        local = window.start_local
        scan_local = scan_utc.astimezone(window.tz)
        row["minute_in_contract"] = (900.0 - seconds_remaining) / 60.0
        # Hour encoded on a circle so 23:00 and 00:00 are adjacent rather than
        # maximally distant.
        hour = scan_local.hour + scan_local.minute / 60.0
        row["hour_of_day_sin"] = float(np.sin(2 * np.pi * hour / 24.0))
        row["hour_of_day_cos"] = float(np.cos(2 * np.pi * hour / 24.0))
        row["is_weekend"] = float(local.weekday() >= 5)

        return row


def select_usable_features(
    table: pd.DataFrame,
    features: Sequence[str],
    *,
    max_missing_fraction: float = 0.02,
) -> tuple[list[str], dict[str, float]]:
    """Drop FEATURES that are missing too often, rather than dropping rows.

    This ordering matters more than it looks. Some features are structurally
    undefined early in a contract — ``buffer_velocity`` needs six bars inside
    the window, so it does not exist at 840 seconds remaining. Dropping rows to
    satisfy it would silently delete every early-contract scan, biasing the
    dataset toward late scans and corrupting exactly the entry-time analysis
    Phase 4 section 7 asks for.

    So: first discard features whose missingness exceeds the threshold, then
    drop the few remaining incomplete rows. Both decisions are reported.
    """
    present = [f for f in features if f in table.columns]
    if not present:
        return [], {}

    values = table[present].to_numpy(dtype="float64", na_value=np.nan)
    finite = np.isfinite(values)
    missing = {
        feature: float(1.0 - finite[:, i].mean())
        for i, feature in enumerate(present)
    }
    dropped = {f: rate for f, rate in missing.items() if rate > max_missing_fraction}
    kept = [f for f in present if f not in dropped]
    return kept, dropped


def drop_incomplete(
    table: pd.DataFrame,
    features: Sequence[str],
    *,
    max_missing_fraction: float = 0.02,
) -> tuple[pd.DataFrame, dict]:
    """Drop rows with non-finite values in the requested features.

    Imputation is deliberately NOT used. A silently imputed feature is how a
    model learns from data that did not exist — the same failure mode the Phase
    2 quality gate exists to prevent. If a feature is missing too often to drop
    rows over, the honest response is to drop the FEATURE, and the returned
    report says which ones would qualify.
    """
    present = [f for f in features if f in table.columns]
    if not present:
        return table, {"dropped": 0, "kept": len(table), "problem_features": {}}

    values = table[present].to_numpy(dtype="float64", na_value=np.nan)
    finite = np.isfinite(values)

    missing_by_feature = {
        feature: float(1.0 - finite[:, i].mean())
        for i, feature in enumerate(present)
    }
    problem = {
        f: rate for f, rate in missing_by_feature.items() if rate > max_missing_fraction
    }

    keep = finite.all(axis=1)
    cleaned = table.loc[keep].reset_index(drop=True)
    return cleaned, {
        "dropped": int((~keep).sum()),
        "kept": int(keep.sum()),
        "missing_by_feature": missing_by_feature,
        "problem_features": problem,
    }
