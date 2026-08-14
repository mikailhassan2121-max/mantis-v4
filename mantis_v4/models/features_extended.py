"""
MANTIS V4 — extended feature families for Phase 4.

Adds the families Phase 4 section 3 asks for, on top of the Phase 3 base:
volatility over multiple horizons, EMA slopes, rolling extrema, range
expansion, cross-asset context, and reference-path statistics.

EVERY FORMULA HERE IS CAUSAL.

Phase 3 established that precomputing indicators over the full series and
reading row ``t`` is identical to computing over ``frame[:t]`` — but ONLY for
causal formulas. That property is what lets the whole pipeline run at
reasonable speed without leaking. Every column added here uses ``ewm``,
``rolling``, ``shift``, ``diff`` or ``pct_change`` and nothing else.

``test_extended_indicators_are_causal`` re-runs the Phase 3 mutation proof
against these columns: it multiplies all FUTURE bars by 5x and requires that no
past row changes. If a non-causal feature is ever added here, that test fails.

CROSS-ASSET FEATURES AND THE CORRELATION PROBLEM

Phase 3 measured 0.641 mean pairwise 1-minute return correlation and only 1.72
effective independent assets out of five. Cross-asset features are therefore
NOT optional context — the coins are largely one asset wearing five coats, and
a model that ignores that is misspecified.

But they are also a leakage hazard of their own: a cross-asset feature reads
OTHER assets' bars at the same instant. Those must be subject to the identical
cutoff rule, which is why the panel is built once on a shared timestamp index
and read positionally at the same cutoff as the primary asset.
"""

from __future__ import annotations

from typing import Optional, Sequence

import numpy as np
import pandas as pd

EXTENDED_COLUMNS = [
    # multi-horizon realised volatility (return space)
    "rv_5m", "rv_15m", "rv_30m", "rv_ewma",
    "vol_ratio_short_long",
    # range / compression
    "range_5m", "range_15m", "range_expansion",
    "atr_ratio",
    # EMA structure
    "ema8", "ema_slope_5", "ema_slope_21",
    "ema_stack_score",
    # extrema
    "dist_from_high_20", "dist_from_low_20",
    "dist_from_high_60", "dist_from_low_60",
    # momentum extras
    "mom2", "mom8",
    "mom_norm_1", "mom_norm_3", "mom_norm_5",
    "trend_persistence",
]

CROSS_ASSET_COLUMNS = [
    "xa_btc_mom5",
    "xa_market_mom5",
    "xa_dispersion",
    "xa_breadth",
    "xa_rel_strength",
    "xa_common_direction",
]


def precompute_extended(frame: pd.DataFrame) -> pd.DataFrame:
    """Extended per-bar indicators. Causal throughout."""
    if frame is None or len(frame) == 0:
        return pd.DataFrame(columns=EXTENDED_COLUMNS)

    closes = frame["Close"].astype(float)
    highs = frame["High"].astype(float)
    lows = frame["Low"].astype(float)

    out = pd.DataFrame(index=frame.index)

    # Gap-aware log returns (audit D-5): a gap-spanning return is not a
    # 1-minute return and must not be treated as one.
    log_returns = np.log(closes / closes.shift(1))
    deltas = frame.index.to_series().diff().dt.total_seconds()
    adjacent = (deltas - 60.0).abs() <= 5.0
    masked = log_returns.where(adjacent)

    out["rv_5m"] = masked.rolling(5, min_periods=3).std(ddof=1)
    out["rv_15m"] = masked.rolling(15, min_periods=8).std(ddof=1)
    out["rv_30m"] = masked.rolling(30, min_periods=15).std(ddof=1)
    out["rv_ewma"] = masked.ewm(span=20, adjust=False).std()

    # Short vs long volatility: > 1 means volatility is expanding.
    out["vol_ratio_short_long"] = out["rv_5m"] / out["rv_30m"].replace(0, np.nan)

    high5 = highs.rolling(5, min_periods=2).max()
    low5 = lows.rolling(5, min_periods=2).min()
    high15 = highs.rolling(15, min_periods=5).max()
    low15 = lows.rolling(15, min_periods=5).min()
    out["range_5m"] = (high5 - low5) / closes
    out["range_15m"] = (high15 - low15) / closes
    out["range_expansion"] = out["range_5m"] / out["range_15m"].replace(0, np.nan)

    true_range = pd.concat(
        [highs - lows,
         (highs - closes.shift(1)).abs(),
         (lows - closes.shift(1)).abs()],
        axis=1,
    ).max(axis=1)
    atr14 = true_range.ewm(alpha=1 / 14, adjust=False).mean()
    atr60 = true_range.ewm(alpha=1 / 60, adjust=False).mean()
    out["atr_ratio"] = atr14 / atr60.replace(0, np.nan)

    ema5 = closes.ewm(span=5, adjust=False).mean()
    ema8 = closes.ewm(span=8, adjust=False).mean()
    ema21 = closes.ewm(span=21, adjust=False).mean()
    out["ema8"] = ema8
    # Slopes normalised by price so they are comparable across assets whose
    # nominal prices differ by five orders of magnitude (BTC ~63000, ADA ~0.18).
    out["ema_slope_5"] = (ema5 - ema5.shift(3)) / closes
    out["ema_slope_21"] = (ema21 - ema21.shift(5)) / closes

    ema3 = closes.ewm(span=3, adjust=False).mean()
    ema13 = closes.ewm(span=13, adjust=False).mean()
    stack = (
        (ema3 > ema5).astype(float)
        + (ema5 > ema8).astype(float)
        + (ema8 > ema13).astype(float)
        + (ema13 > ema21).astype(float)
    )
    out["ema_stack_score"] = stack - 2.0   # centred: -2 (full bear) .. +2 (full bull)

    high20 = highs.rolling(20, min_periods=5).max()
    low20 = lows.rolling(20, min_periods=5).min()
    high60 = highs.rolling(60, min_periods=15).max()
    low60 = lows.rolling(60, min_periods=15).min()
    out["dist_from_high_20"] = (closes - high20) / closes
    out["dist_from_low_20"] = (closes - low20) / closes
    out["dist_from_high_60"] = (closes - high60) / closes
    out["dist_from_low_60"] = (closes - low60) / closes

    out["mom2"] = closes.pct_change(2)
    out["mom8"] = closes.pct_change(8)

    # Volatility-normalised momentum: a 0.1% move means something very
    # different in a calm minute than in a violent one.
    denom = out["rv_15m"].replace(0, np.nan)
    out["mom_norm_1"] = closes.pct_change(1) / denom
    out["mom_norm_3"] = (closes.pct_change(3) / 3.0) / denom
    out["mom_norm_5"] = (closes.pct_change(5) / 5.0) / denom

    # Trend persistence: net sign agreement over the last 10 bars, in [-1, 1].
    signs = np.sign(closes.diff())
    out["trend_persistence"] = signs.rolling(10, min_periods=5).mean()

    return out.replace([np.inf, -np.inf], np.nan)


def build_cross_asset_panel(
    frames: dict[str, pd.DataFrame],
    reference_asset: str = "BTC-USD",
) -> Optional[pd.DataFrame]:
    """Per-timestamp cross-asset context, aligned on a shared index.

    Read at the SAME cutoff as the primary asset, so every cross-asset value is
    subject to the identical "completed bars only" rule.

    Returns a frame indexed by timestamp with one block of columns per asset:
    ``{asset}__mom5`` plus the shared market aggregates.
    """
    if not frames:
        return None

    mom5 = {}
    for asset, frame in frames.items():
        closes = frame["Close"].astype(float)
        mom5[asset] = closes.pct_change(5)

    panel = pd.DataFrame(mom5).sort_index()
    if panel.empty:
        return None

    assets = list(panel.columns)
    market_mean = panel.mean(axis=1, skipna=True)
    dispersion = panel.std(axis=1, ddof=1, skipna=True)
    breadth = (panel > 0).sum(axis=1) / panel.notna().sum(axis=1).replace(0, np.nan)

    out = pd.DataFrame(index=panel.index)
    out["xa_market_mom5"] = market_mean
    out["xa_dispersion"] = dispersion
    out["xa_breadth"] = breadth
    out["xa_common_direction"] = np.sign(market_mean)
    out["xa_btc_mom5"] = (
        panel[reference_asset] if reference_asset in panel.columns else market_mean
    )
    for asset in assets:
        # Relative strength: this asset's move minus the common factor. With
        # 0.641 mean correlation, the residual is where asset-specific
        # information actually lives.
        out[f"{asset}__rel_strength"] = panel[asset] - market_mean
        out[f"{asset}__mom5"] = panel[asset]

    return out.replace([np.inf, -np.inf], np.nan)


def reference_path_features(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    bar_times: np.ndarray,
    reference: float,
    scan_epoch: float,
) -> dict[str, float]:
    """Statistics of the price path relative to the reference, within a window.

    ``closes``/``highs``/``lows`` cover ONLY bars from the window start up to
    the scan cutoff. Phase 4 section 3 asks for crossing count, time since last
    crossing, and fraction of the contract spent above the reference.

    These matter because a contract whose price has oscillated across the
    reference eight times is a fundamentally different object from one that
    moved away once and stayed there, even when the two have an identical
    current buffer.
    """
    n = len(closes)
    if n == 0 or reference <= 0:
        return {
            "path_bars": 0.0,
            "frac_above_reference": np.nan,
            "crossings": np.nan,
            "seconds_since_crossing": np.nan,
            "crossing_intensity": np.nan,
            "max_favourable_buffer": np.nan,
            "max_adverse_buffer": np.nan,
            "buffer_velocity": np.nan,
        }

    above = closes > reference
    frac_above = float(above.mean())

    changes = np.flatnonzero(above[1:] != above[:-1])
    crossings = float(len(changes))

    if len(changes):
        last_cross_time = float(bar_times[changes[-1] + 1])
        seconds_since = max(0.0, scan_epoch - last_cross_time)
    else:
        seconds_since = max(0.0, scan_epoch - float(bar_times[0]))

    elapsed = max(1.0, scan_epoch - float(bar_times[0]))
    intensity = crossings / (elapsed / 60.0)

    max_favourable = float((highs.max() - reference) / reference)
    max_adverse = float((lows.min() - reference) / reference)

    # Is the buffer expanding or collapsing? Compare the current buffer with
    # the buffer five bars ago.
    if n >= 6:
        current = (closes[-1] - reference) / reference
        earlier = (closes[-6] - reference) / reference
        velocity = float(current - earlier)
    else:
        velocity = np.nan

    return {
        "path_bars": float(n),
        "frac_above_reference": frac_above,
        "crossings": crossings,
        "seconds_since_crossing": seconds_since,
        "crossing_intensity": float(intensity),
        "max_favourable_buffer": max_favourable,
        "max_adverse_buffer": max_adverse,
        "buffer_velocity": velocity,
    }


PATH_COLUMNS = [
    "path_bars",
    "frac_above_reference",
    "crossings",
    "seconds_since_crossing",
    "crossing_intensity",
    "max_favourable_buffer",
    "max_adverse_buffer",
    "buffer_velocity",
]
