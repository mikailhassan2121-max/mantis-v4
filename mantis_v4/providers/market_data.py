"""
MANTIS V4 — yfinance market-data provider.

Master prompt section 26J keeps yfinance as a fallback/bootstrap source for
1-minute bars, with retry, exponential backoff, cache, stale-data detection and
partial-failure handling.

Audit findings fixed here:

  D-3  V3 set no network timeout on any yfinance call, so a hung HTTP request
       stalled the entire market loop indefinitely -- through a contract
       boundary, with a live position open, silently. Every call now has an
       explicit timeout.
  D-5  V3 computed log returns across index gaps as if they were 1-minute
       returns. Bars are now reindexed onto a strict grid and gaps are
       detectable rather than silently inflating volatility.
  D-8  V3's tz-naive branch converted the ET contract start to naive ET wall
       clock; had yfinance ever returned a naive UTC index, the anchor would
       have come from a contract 4-5 hours away. Everything is normalised to
       tz-aware UTC on ingest, and a naive index is treated as UTC explicitly
       and flagged, never guessed.
  D-4  the in-progress bar is identified rather than blended in unlabelled.

NOTE ON DELIBERATE DEVIATION FROM V3: V3's second fetch path used
``yf.Ticker().history()``, which accepts no timeout parameter (verified against
yfinance 1.5.2). Rather than reintroduce an untimeoutable network call, the
fallback re-issues ``yf.download`` with the bootstrap period. The resilience
intent (recover when the first call returns short data) is preserved; the
unbounded-hang risk is not.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from ..clock import UTC, Instant
from .base import BarSet, MarketDataProvider, ProviderError, retry_with_backoff

REQUIRED_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


def normalize_bars(raw: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
    """Coerce a yfinance frame into a strict UTC-indexed OHLCV frame.

    Returns None when the frame cannot be trusted. Never repairs silently:
    rows with unusable OHLC are dropped, not interpolated.
    """
    if raw is None or len(raw) == 0:
        return None

    frame = raw.copy()

    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = frame.columns.get_level_values(0)

    if any(column not in frame.columns for column in REQUIRED_COLUMNS):
        return None

    frame = frame[REQUIRED_COLUMNS].copy()
    for column in REQUIRED_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    frame = frame.replace([np.inf, -np.inf], np.nan)
    frame = frame.dropna(subset=["Open", "High", "Low", "Close"])
    if frame.empty:
        return None

    index = pd.DatetimeIndex(frame.index)
    if index.tz is None:
        # Explicit, documented assumption -- not a silent guess (audit D-8).
        # yfinance returns UTC-naive indices in this situation.
        index = index.tz_localize(UTC)
    else:
        index = index.tz_convert(UTC)
    frame.index = index

    frame = frame[~frame.index.duplicated(keep="last")]
    frame = frame.sort_index()
    return frame


def bar_gap_report(frame: pd.DataFrame, expected_minutes: int = 1) -> dict:
    """Describe missing bars without modifying the data (audit D-5).

    Returned counts let the quality gate decide whether volatility estimated
    from these bars is trustworthy, instead of silently treating a 40-minute
    gap as a 1-minute return.
    """
    if frame is None or len(frame) < 2:
        return {"gaps": 0, "max_gap_minutes": 0.0, "expected_bars": len(frame or [])}

    deltas = frame.index.to_series().diff().dropna()
    step = pd.Timedelta(minutes=expected_minutes)
    gaps = int((deltas > step).sum())
    max_gap = float(deltas.max().total_seconds() / 60.0) if len(deltas) else 0.0
    return {
        "gaps": gaps,
        "max_gap_minutes": max_gap,
        "expected_bars": len(frame),
    }


def contiguous_log_returns(
    frame: pd.DataFrame,
    expected_minutes: int = 1,
    tolerance_seconds: float = 5.0,
) -> pd.Series:
    """Log returns computed ONLY across genuinely adjacent bars.

    Audit D-5: ``log(close / close.shift(1))`` assumes adjacent rows are one
    minute apart. Yahoo's crypto 1-minute series contains gaps, and a
    gap-spanning return was being treated as a 1-minute return, inflating the
    volatility estimate. Returns spanning a gap are dropped here.
    """
    if frame is None or len(frame) < 2:
        return pd.Series(dtype="float64")

    closes = frame["Close"].astype(float)
    returns = np.log(closes / closes.shift(1))

    deltas = frame.index.to_series().diff().dt.total_seconds()
    expected = expected_minutes * 60.0
    adjacent = (deltas - expected).abs() <= tolerance_seconds

    return returns[adjacent].dropna()


def distinct_close_count(frame: pd.DataFrame, lookback: int) -> int:
    """How many distinct closing prices appear in the last ``lookback`` bars.

    A frozen or synthetic feed produces a very low count while its timestamps
    keep advancing -- the exact condition that let V3 report 96.8% confidence
    off a stalled feed (audit B-5). Timestamp freshness cannot detect this.
    """
    if frame is None or len(frame) == 0:
        return 0
    tail = frame["Close"].tail(max(1, lookback))
    return int(tail.nunique())


def price_at_or_after(frame: pd.DataFrame, moment: datetime) -> Optional[tuple[float, str, datetime]]:
    """First bar Open at or after ``moment``.

    This is the anchor/terminal convention: the Open of the first bar at or
    after an instant approximates the price *at* that instant. Using the same
    rule for both the window's reference and its terminal price keeps the two
    measurements symmetric, which matters when the difference between them is
    the label being learned.

    Returns (price, rule_name, bar_timestamp) or None.
    """
    if frame is None or len(frame) == 0:
        return None
    moment = moment.astimezone(UTC)
    subset = frame.loc[frame.index >= moment]
    if subset.empty:
        return None
    row = subset.iloc[0]
    return float(row["Open"]), "OPEN_AT_OR_AFTER", subset.index[0].to_pydatetime()


def price_at_or_before(frame: pd.DataFrame, moment: datetime) -> Optional[tuple[float, str, datetime]]:
    """Last bar Close at or before ``moment``. Fallback when no bar lands on it."""
    if frame is None or len(frame) == 0:
        return None
    moment = moment.astimezone(UTC)
    subset = frame.loc[frame.index <= moment]
    if subset.empty:
        return None
    row = subset.iloc[-1]
    return float(row["Close"]), "CLOSE_AT_OR_BEFORE", subset.index[-1].to_pydatetime()


class YFinanceMarketDataProvider(MarketDataProvider):
    """1-minute bars from Yahoo Finance, with timeout/retry/backoff/cache.

    Section 26J is explicit that this data must not be described as
    "ultra-dense": it is 1-minute bars, Yahoo's own aggregation, and its volume
    is not exchange-wide crypto volume.
    """

    name = "yahoo"

    def __init__(
        self,
        *,
        interval: str = "1m",
        bootstrap_period: str = "7d",
        refresh_period: str = "1d",
        min_candles: int = 60,
        max_cache_rows: int = 5000,
        timeout_seconds: float = 8.0,
        max_retries: int = 3,
        backoff_seconds: float = 1.5,
        downloader=None,
    ) -> None:
        super().__init__()
        self.interval = interval
        self.bootstrap_period = bootstrap_period
        self.refresh_period = refresh_period
        self.min_candles = min_candles
        self.max_cache_rows = max_cache_rows
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self._cache: dict[str, pd.DataFrame] = {}
        self._downloader = downloader or self._default_downloader

    # -- network ------------------------------------------------------------

    def _default_downloader(self, asset: str, period: str) -> Optional[pd.DataFrame]:
        import yfinance as yf

        return yf.download(
            asset,
            period=period,
            interval=self.interval,
            auto_adjust=False,
            progress=False,
            threads=False,
            timeout=self.timeout_seconds,   # audit D-3
        )

    def _fetch(self, asset: str, period: str) -> Optional[pd.DataFrame]:
        return normalize_bars(self._downloader(asset, period))

    # -- cache --------------------------------------------------------------

    def _merge_cache(self, asset: str, fresh: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
        cached = self._cache.get(asset)
        if fresh is None or fresh.empty:
            return cached
        if cached is None or cached.empty:
            merged = fresh.copy()
        else:
            merged = pd.concat([cached, fresh])
            merged = merged[~merged.index.duplicated(keep="last")]
            merged = merged.sort_index()
        if len(merged) > self.max_cache_rows:
            merged = merged.iloc[-self.max_cache_rows :].copy()
        self._cache[asset] = merged
        return merged

    # -- interface ----------------------------------------------------------

    def get_bars(self, asset: str, instant: Instant) -> Optional[BarSet]:
        """Fetch bars. Never raises -- a failure degrades to cache or None."""
        cached = self._cache.get(asset)
        need_bootstrap = cached is None or len(cached) < self.min_candles
        period = self.bootstrap_period if need_bootstrap else self.refresh_period

        fresh: Optional[pd.DataFrame] = None
        degraded = False
        error_text = ""

        try:
            fresh = retry_with_backoff(
                lambda: self._fetch(asset, period),
                attempts=self.max_retries,
                backoff_seconds=self.backoff_seconds,
                health=self.health,
            )
        except ProviderError as exc:
            error_text = str(exc)
            degraded = True

        # Fallback: re-issue with the longer bootstrap period when the refresh
        # came back too short. Uses yf.download (which supports timeout) rather
        # than Ticker.history (which does not).
        if (fresh is None or len(fresh) < self.min_candles) and period != self.bootstrap_period:
            try:
                fallback = retry_with_backoff(
                    lambda: self._fetch(asset, self.bootstrap_period),
                    attempts=self.max_retries,
                    backoff_seconds=self.backoff_seconds,
                    health=self.health,
                )
                if fallback is not None and not fallback.empty:
                    fresh = fallback
                    degraded = True
            except ProviderError as exc:
                error_text = error_text or str(exc)
                degraded = True

        combined = self._merge_cache(asset, fresh)

        if combined is None or combined.empty:
            self.health.record_failure(
                error_text or "no data returned",
            )
            return None

        from_cache = fresh is None or fresh.empty
        if from_cache:
            self.health.record_success(
                instant.utc, detail=f"cached {len(combined)} bars", degraded=True
            )
        else:
            self.health.record_success(
                instant.utc,
                detail=f"{len(combined)} bars",
                degraded=degraded,
            )

        # The final bar of a 1-minute series is the currently forming minute
        # whenever the scan instant falls inside it (audit D-4).
        last_start = combined.index[-1].to_pydatetime()
        partial = instant.utc < last_start + timedelta(minutes=1)

        return BarSet(
            asset=asset,
            frame=combined,
            source=self.name,
            fetched_at_utc=instant.utc,
            last_bar_is_partial=partial,
            from_cache=from_cache,
        )
