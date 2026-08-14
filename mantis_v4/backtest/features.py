"""
MANTIS V4 — leakage-safe feature computation (Phase 3 requirement 7).

TWO FEATURE FAMILIES, DELIBERATELY KEPT SEPARATE

1.  ``v3_*`` — a FAITHFUL reproduction of V3's arithmetic, bugs included.
    A "V3 baseline" that quietly fixes V3's defects is not a V3 baseline, and
    comparing V4 against a repaired V3 would flatter V4. Two V3 defects are
    reproduced on purpose and marked V3-BUG-FAITHFUL:

      * ``v3_rsi`` returns 50 on a zero-loss window. Audit D-9 confirmed the
        correct answer is 100; V3's ``fillna(50)`` reports neutral.
      * ``v3_realized_vol_1m`` computes log returns across index gaps as if
        they were 1-minute returns (audit D-5), inflating volatility.

2.  everything else — the corrected V4 versions: ``rsi`` uses the proper
    zero-loss convention and ``realized_vol_1m`` uses only contiguous returns.

Master prompt section 26C additionally requires that price-unit and return-unit
volatility never be mixed without conversion. Both are computed and explicitly
named: ``sigma_remaining_return`` and ``sigma_remaining_price``.

WHY INDICATORS ARE PRECOMPUTED, AND WHY THAT IS STILL LEAKAGE-SAFE

The first implementation recomputed every indicator from the full visible
history at each of ~800,000 scans. Visible history grows to 40,000 bars, so
that was O(N) work per scan and the run did not finish.

``precompute_indicators`` instead computes each indicator ONCE per asset over
the whole series, and each scan reads the row belonging to its last visible
bar.

That is not a shortcut around the leakage barrier, because every formula used
here is CAUSAL: ``ewm``, ``diff``, ``pct_change`` and ``rolling`` at row ``t``
depend only on rows ``<= t``. For such formulas, computing over ``frame[:t]``
and reading the last row is mathematically identical to computing over the full
frame and reading row ``t``.

That identity is the load-bearing claim of this optimisation, so it is PROVEN
by test rather than asserted: ``test_precomputed_indicators_match_truncated``
recomputes indicators from truncated history at many random scan points and
requires an exact match against the precomputed row. If a non-causal feature is
ever added, that test fails immediately.

The scan-time geometry (buffer, z-scores, sigma over remaining time) still
depends on the scan instant and the contract, so it is computed per scan — but
it is cheap scalar arithmetic.
"""

from __future__ import annotations

import math
from typing import Any, Optional

import numpy as np
import pandas as pd

from ..contracts import ContractWindow
from .view import HistoricalMarketView, ProxyReference

# V3 constants, reproduced verbatim so the baseline is genuinely V3.
V3_VOL_FLOOR = 0.00001
V3_MIN_MINUTES_LEFT = 0.20
V3_TREND_CLIP = 0.55
V3_TREND_DIVISOR = 10.0

MIN_VOL_OBSERVATIONS = 10


def normal_cdf(z: float) -> float:
    """Standard normal CDF. Matches V3's math.erf implementation exactly."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


# ---------------------------------------------------------------------------
# Scalar helpers (kept for direct testing of the V3 bug reproductions)
# ---------------------------------------------------------------------------

def compute_v3_rsi(closes: pd.Series, period: int = 14) -> float:
    """V3-BUG-FAITHFUL RSI (audit D-9): 50 instead of 100 on a zero-loss window."""
    return float(v3_rsi_series(closes, period).iloc[-1])


def compute_rsi(closes: pd.Series, period: int = 14) -> float:
    """Corrected RSI: 100 on a pure uptrend."""
    return float(rsi_series(closes, period).iloc[-1])


def v3_rsi_series(closes: pd.Series, period: int = 14) -> pd.Series:
    change = closes.diff()
    gain = change.clip(lower=0)
    loss = -change.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - (100 / (1 + rs))).fillna(50)


def rsi_series(closes: pd.Series, period: int = 14) -> pd.Series:
    change = closes.diff()
    gain = change.clip(lower=0)
    loss = -change.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    # avg_loss == 0 -> 100 when there were gains, else neutral 50.
    zero_loss = avg_loss <= 0
    rsi = rsi.where(~zero_loss, np.where(avg_gain > 0, 100.0, 50.0))
    return rsi.fillna(50.0)


def contiguous_returns(frame: pd.DataFrame, tolerance_seconds: float = 5.0) -> pd.Series:
    """Log returns across genuinely adjacent 1-minute bars only (audit D-5)."""
    if frame is None or len(frame) < 2:
        return pd.Series(dtype="float64")
    closes = frame["Close"].astype(float)
    returns = np.log(closes / closes.shift(1))
    deltas = frame.index.to_series().diff().dt.total_seconds()
    adjacent = (deltas - 60.0).abs() <= tolerance_seconds
    return returns[adjacent].dropna()


def naive_returns(frame: pd.DataFrame) -> pd.Series:
    """V3-BUG-FAITHFUL: log returns that ignore gaps entirely."""
    if frame is None or len(frame) < 2:
        return pd.Series(dtype="float64")
    closes = frame["Close"].astype(float)
    return np.log(closes / closes.shift(1)).dropna()


# ---------------------------------------------------------------------------
# Vectorised precomputation
# ---------------------------------------------------------------------------

INDICATOR_COLUMNS = [
    "close", "open", "high", "low", "volume",
    "ema3", "ema5", "ema9", "ema13", "ema21", "ema5_minus_ema21",
    "macd", "macd_signal", "macd_hist", "macd_hist_previous", "macd_hist_change",
    "rsi", "v3_rsi", "atr",
    "mom1", "mom3", "mom5", "mom10", "momentum_acceleration",
    "close_location", "body_pct", "volume_ratio",
    "vol20", "vol60", "realized_vol_1m", "ewma_vol_1m",
    "v3_vol20", "v3_vol60", "v3_realized_vol_1m",
    "v3_trend_points",
]


def precompute_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    """Compute every bar-level indicator once, vectorised.

    Every formula here is causal: row ``t`` depends only on rows ``<= t``.
    ``test_precomputed_indicators_match_truncated`` proves it.
    """
    if frame is None or len(frame) == 0:
        return pd.DataFrame(columns=INDICATOR_COLUMNS)

    closes = frame["Close"].astype(float)
    opens = frame["Open"].astype(float)
    highs = frame["High"].astype(float)
    lows = frame["Low"].astype(float)
    volumes = frame["Volume"].astype(float)

    out = pd.DataFrame(index=frame.index)
    out["close"] = closes
    out["open"] = opens
    out["high"] = highs
    out["low"] = lows
    out["volume"] = volumes

    for span in (3, 5, 9, 13, 21):
        out[f"ema{span}"] = closes.ewm(span=span, adjust=False).mean()
    out["ema5_minus_ema21"] = out["ema5"] - out["ema21"]

    ema12 = closes.ewm(span=12, adjust=False).mean()
    ema26 = closes.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    hist = macd - signal
    out["macd"] = macd
    out["macd_signal"] = signal
    out["macd_hist"] = hist
    out["macd_hist_previous"] = hist.shift(1)
    out["macd_hist_change"] = hist - hist.shift(1)

    out["rsi"] = rsi_series(closes)
    out["v3_rsi"] = v3_rsi_series(closes)

    previous_close = closes.shift(1)
    true_range = pd.concat(
        [highs - lows, (highs - previous_close).abs(), (lows - previous_close).abs()],
        axis=1,
    ).max(axis=1)
    out["atr"] = true_range.ewm(alpha=1 / 14, adjust=False).mean()

    for horizon in (1, 3, 5, 10):
        out[f"mom{horizon}"] = closes.pct_change(horizon)

    recent_rate = 0.55 * out["mom1"] + 0.45 * out["mom3"] / 3.0
    broader_rate = 0.60 * out["mom5"] / 5.0 + 0.40 * out["mom10"] / 10.0
    out["momentum_acceleration"] = recent_rate - broader_rate

    candle_range = (highs - lows).replace(0, np.nan)
    out["close_location"] = ((closes - lows) / candle_range).fillna(0.5)
    out["body_pct"] = ((closes - opens) / opens.replace(0, np.nan)).fillna(0.0)

    volume_average = volumes.rolling(20, min_periods=1).mean()
    out["volume_ratio"] = (
        (volumes / volume_average.replace(0, np.nan))
        .replace([np.inf, -np.inf], np.nan)
        .fillna(1.0)
    )

    # -- volatility: corrected (gap-aware) ---------------------------------
    log_returns = np.log(closes / closes.shift(1))
    deltas = frame.index.to_series().diff().dt.total_seconds()
    adjacent = (deltas - 60.0).abs() <= 5.0
    masked = log_returns.where(adjacent)
    out["vol20"] = masked.rolling(20, min_periods=MIN_VOL_OBSERVATIONS).std(ddof=1)
    out["vol60"] = masked.rolling(60, min_periods=MIN_VOL_OBSERVATIONS).std(ddof=1)
    out["realized_vol_1m"] = 0.70 * out["vol20"] + 0.30 * out["vol60"].fillna(out["vol20"])
    out["ewma_vol_1m"] = masked.ewm(span=30, adjust=False).std()

    # -- volatility: V3-BUG-FAITHFUL (spans gaps) --------------------------
    v3_returns = log_returns
    out["v3_vol20"] = v3_returns.rolling(20, min_periods=MIN_VOL_OBSERVATIONS).std(ddof=1)
    out["v3_vol60"] = v3_returns.rolling(60, min_periods=MIN_VOL_OBSERVATIONS).std(ddof=1)
    v3_vol20 = out["v3_vol20"].fillna(0.0)
    v3_vol60 = out["v3_vol60"].fillna(v3_vol20)
    out["v3_realized_vol_1m"] = np.maximum(V3_VOL_FLOOR, 0.70 * v3_vol20 + 0.30 * v3_vol60)

    # -- V3 trend tally, vectorised ----------------------------------------
    points = pd.Series(0.0, index=frame.index)
    bull = (out["ema5"] > out["ema9"]) & (out["ema9"] > out["ema21"])
    bear = (out["ema5"] < out["ema9"]) & (out["ema9"] < out["ema21"])
    points += np.where(bull, 2.0, np.where(bear, -2.0, 0.0))
    points += np.where(out["macd_hist"] > 0, 0.8, -0.8)
    points += np.where(out["macd_hist_change"] > 0, 0.35, -0.35)
    for key, weight in (("mom1", 0.20), ("mom3", 0.45), ("mom5", 0.60), ("mom10", 0.70)):
        column = out[key]
        points += np.where(column > 0, weight, np.where(column < 0, -weight, 0.0))
    rsi_v3 = out["v3_rsi"]
    points += np.where(
        (rsi_v3 >= 54) & (rsi_v3 <= 72), 0.35,
        np.where((rsi_v3 >= 28) & (rsi_v3 <= 46), -0.35, 0.0),
    )
    acceleration = out["momentum_acceleration"]
    points += np.where(acceleration > 0, 0.30, np.where(acceleration < 0, -0.30, 0.0))
    out["v3_trend_points"] = points

    return out


# ---------------------------------------------------------------------------
# Per-scan features
# ---------------------------------------------------------------------------

def compute_features(
    view: HistoricalMarketView,
    window: ContractWindow,
    reference: Optional[ProxyReference],
    *,
    indicators: Optional[pd.DataFrame] = None,
    min_bars: int = 60,
) -> Optional[dict[str, Any]]:
    """All baseline features at one scan instant, or None if not computable.

    ``indicators`` is the precomputed frame from ``precompute_indicators``. When
    omitted, indicators are derived from the view's own (truncated) history —
    the slow path, retained because the equivalence test compares the two.

    Returning None rather than a partially-filled dict is deliberate: a feature
    dict with silent zeros is how a model learns from data that did not exist.
    """
    if view.is_empty or len(view) < min_bars:
        return None

    last_index = view.frame.index[-1]

    if indicators is None:
        indicators = precompute_indicators(view.frame)
        if indicators.empty:
            return None
        row = indicators.iloc[-1]
    else:
        try:
            row = indicators.loc[last_index]
        except KeyError:
            return None

    spot = float(row["close"])
    if not np.isfinite(spot) or spot <= 0:
        return None

    scan_utc = view.scan_utc
    seconds_remaining = window.seconds_remaining(scan_utc)
    minutes_left = seconds_remaining / 60.0

    def value(name: str) -> float:
        raw = row.get(name, np.nan)
        return float(raw) if raw is not None else float("nan")

    realized_vol_1m = value("realized_vol_1m")
    v3_remaining_sigma = value("v3_realized_vol_1m") * math.sqrt(
        max(minutes_left, V3_MIN_MINUTES_LEFT)
    )

    # Section 26C: return-space and price-space volatility kept distinct.
    if np.isfinite(realized_vol_1m):
        sigma_remaining_return = realized_vol_1m * math.sqrt(max(minutes_left, 0.0))
        sigma_remaining_price = sigma_remaining_return * spot
    else:
        sigma_remaining_return = float("nan")
        sigma_remaining_price = float("nan")

    features: dict[str, Any] = {
        "seconds_remaining": seconds_remaining,
        "elapsed_seconds": window.elapsed_seconds(scan_utc),
        "elapsed_fraction": window.elapsed_fraction(scan_utc),
        "minutes_left": minutes_left,
        "spot": spot,
        "spot_age_seconds": view.spot_age_seconds,
        "visible_bars": len(view),
        "ema3": value("ema3"), "ema5": value("ema5"), "ema9": value("ema9"),
        "ema13": value("ema13"), "ema21": value("ema21"),
        "ema5_minus_ema21": value("ema5_minus_ema21"),
        "macd": value("macd"), "macd_signal": value("macd_signal"),
        "macd_hist": value("macd_hist"),
        "macd_hist_previous": value("macd_hist_previous"),
        "macd_hist_change": value("macd_hist_change"),
        "rsi": value("rsi"), "v3_rsi": value("v3_rsi"),
        "atr": value("atr"),
        "atr_pct": value("atr") / spot,
        "mom1": value("mom1"), "mom3": value("mom3"),
        "mom5": value("mom5"), "mom10": value("mom10"),
        "momentum_acceleration": value("momentum_acceleration"),
        "close_location": value("close_location"),
        "body_pct": value("body_pct"),
        "volume_ratio": value("volume_ratio"),
        "vol20": value("vol20"), "vol60": value("vol60"),
        "realized_vol_1m": realized_vol_1m,
        "ewma_vol_1m": value("ewma_vol_1m"),
        "sigma_remaining_return": sigma_remaining_return,
        "sigma_remaining_price": sigma_remaining_price,
        "v3_realized_vol_1m": value("v3_realized_vol_1m"),
        "v3_remaining_sigma_pct": v3_remaining_sigma,
    }

    # -- contract geometry (only when the reference is genuinely known) -----
    if reference is not None and reference.available_at(scan_utc) and reference.price > 0:
        buffer_abs = spot - reference.price
        buffer_pct = buffer_abs / reference.price

        v3_raw_z = buffer_pct / max(v3_remaining_sigma, V3_VOL_FLOOR)
        trend_points = value("v3_trend_points")
        trend_adjustment = max(
            -V3_TREND_CLIP, min(V3_TREND_CLIP, trend_points / V3_TREND_DIVISOR)
        )
        v3_adjusted_z = v3_raw_z + trend_adjustment

        features.update({
            "reference": reference.price,
            "reference_available": True,
            "buffer_abs": buffer_abs,
            "buffer_pct": buffer_pct,
            "v3_raw_z": v3_raw_z,
            "v3_trend_points": trend_points,
            "v3_trend_adjustment": trend_adjustment,
            "v3_adjusted_z": v3_adjusted_z,
            "v3_p_yes": normal_cdf(v3_adjusted_z),
        })

        if np.isfinite(sigma_remaining_return) and sigma_remaining_return > 0:
            normal_z = buffer_pct / sigma_remaining_return
            features["normal_z"] = normal_z
            features["p_yes_normal_baseline"] = normal_cdf(normal_z)
        else:
            features["normal_z"] = float("nan")
            features["p_yes_normal_baseline"] = float("nan")
    else:
        features.update({
            "reference": None, "reference_available": False,
            "buffer_abs": None, "buffer_pct": None,
            "v3_raw_z": None, "v3_trend_points": None,
            "v3_trend_adjustment": None, "v3_adjusted_z": None, "v3_p_yes": None,
            "normal_z": None, "p_yes_normal_baseline": None,
        })

    return features


FEATURE_COLUMNS = [
    "seconds_remaining", "elapsed_seconds", "elapsed_fraction", "minutes_left",
    "spot", "spot_age_seconds", "visible_bars",
    "ema3", "ema5", "ema9", "ema13", "ema21", "ema5_minus_ema21",
    "macd", "macd_signal", "macd_hist", "macd_hist_previous", "macd_hist_change",
    "rsi", "v3_rsi", "atr", "atr_pct",
    "mom1", "mom3", "mom5", "mom10", "momentum_acceleration",
    "close_location", "body_pct", "volume_ratio",
    "vol20", "vol60", "realized_vol_1m", "ewma_vol_1m",
    "sigma_remaining_return", "sigma_remaining_price",
    "v3_realized_vol_1m", "v3_remaining_sigma_pct",
    "reference", "reference_available", "buffer_abs", "buffer_pct",
    "v3_raw_z", "v3_trend_points", "v3_trend_adjustment", "v3_adjusted_z", "v3_p_yes",
    "normal_z", "p_yes_normal_baseline",
]
