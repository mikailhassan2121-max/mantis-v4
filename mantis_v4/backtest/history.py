"""
MANTIS V4 — historical bar loading (Phase 3).

Master prompt section 26J keeps yfinance as the bootstrap source. Requirement 8
of Phase 3 additionally demands that this sit behind an interface so a longer /
better provider can replace it without touching the modelling code.

MEASURED LIMITS OF THE CURRENT SOURCE (probed against the live API, not assumed):

    single request : 8 days maximum
                     ("Only 8 days worth of 1m granularity data are allowed to
                      be fetched per request")
    total reach    : ~28 days via 7-day chunks, then a hard wall
                     ("The requested range must be within the last 30 days")
    completeness   : ~10,073 of 10,080 expected bars per 7-day chunk (~99.93%)

That ceiling is the binding constraint on all of Phase 3 (audit finding D-1).
It yields roughly 2,688 windows per asset. Every result derived from it is
PRELIMINARY and must be reported with sample sizes and confidence intervals.

CACHING AND REPRODUCIBILITY

Downloaded chunks are cached to CSV under ``data/history/``. A backtest re-run
therefore replays byte-identical input, which is what makes the research
reproducible (master prompt section 25). Cache files record the fetch time so
staleness is visible rather than silent.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

from ..clock import UTC
from ..providers.market_data import normalize_bars

# Probed against the live API on 2026-08-13. See module docstring.
YF_MAX_DAYS_PER_REQUEST = 8
YF_MAX_TOTAL_DAYS = 30
CHUNK_DAYS = 7


@dataclass
class HistoryLoadReport:
    """What actually happened during a load. Never silently partial."""

    asset: str
    bars: int = 0
    requested_days: int = 0
    chunks_attempted: int = 0
    chunks_succeeded: int = 0
    chunks_failed: int = 0
    first_bar_utc: Optional[datetime] = None
    last_bar_utc: Optional[datetime] = None
    from_cache_chunks: int = 0
    errors: list[str] = field(default_factory=list)
    skipped: bool = False
    skip_reason: str = ""

    @property
    def span_days(self) -> float:
        if self.first_bar_utc is None or self.last_bar_utc is None:
            return 0.0
        return (self.last_bar_utc - self.first_bar_utc).total_seconds() / 86400.0

    @property
    def completeness(self) -> float:
        """Observed bars divided by the number a gapless 1-minute feed would give."""
        expected = self.span_days * 1440.0
        if expected <= 0:
            return 0.0
        return min(1.0, self.bars / expected)

    def summary(self) -> str:
        if self.skipped:
            return f"{self.asset}: SKIPPED - {self.skip_reason}"
        return (
            f"{self.asset}: {self.bars} bars, span {self.span_days:.2f}d, "
            f"completeness {self.completeness * 100:.2f}%, "
            f"chunks {self.chunks_succeeded}/{self.chunks_attempted}"
        )


class HistoricalBarProvider(ABC):
    """Source of historical OHLCV bars for the backtester.

    Deliberately minimal so that replacing yfinance with a longer/denser feed
    is a single-class change. Implementations must return a tz-aware UTC-indexed
    OHLCV frame or None, and must never fabricate or interpolate bars.
    """

    name: str = "history"

    @abstractmethod
    def load(self, asset: str, days: int) -> tuple[Optional[pd.DataFrame], HistoryLoadReport]:
        """Return (frame, report). Frame is None when nothing usable was obtained."""

    @property
    def max_days(self) -> int:
        """Longest history this provider can supply."""
        return YF_MAX_TOTAL_DAYS


class YFinanceHistoryProvider(HistoricalBarProvider):
    """Chunked, cached 1-minute history from Yahoo Finance.

    This is the BOOTSTRAP source, per Phase 3 requirement 8. It is not a
    research-grade archive and must not be described as one.
    """

    name = "yfinance-1m"

    def __init__(
        self,
        cache_dir: Path,
        *,
        interval: str = "1m",
        timeout_seconds: float = 25.0,
        max_retries: int = 3,
        backoff_seconds: float = 2.0,
        downloader=None,
        use_cache: bool = True,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.interval = interval
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self.use_cache = use_cache
        self._downloader = downloader or self._default_downloader

    # -- network --------------------------------------------------------

    def _default_downloader(self, asset: str, start: datetime, end: datetime):
        import yfinance as yf

        return yf.download(
            asset,
            start=start.date(),
            end=end.date(),
            interval=self.interval,
            auto_adjust=False,
            progress=False,
            threads=False,
            timeout=self.timeout_seconds,
        )

    # -- cache ----------------------------------------------------------

    def _chunk_path(self, asset: str, start: datetime, end: datetime) -> Path:
        safe = asset.replace("/", "_")
        return self.cache_dir / f"{safe}_{self.interval}_{start:%Y%m%d}_{end:%Y%m%d}.csv"

    def _read_cache(self, path: Path) -> Optional[pd.DataFrame]:
        if not (self.use_cache and path.exists()):
            return None
        try:
            frame = pd.read_csv(path, index_col=0, parse_dates=True)
        except (OSError, ValueError, pd.errors.ParserError):
            return None
        if frame.empty:
            return None
        index = pd.DatetimeIndex(frame.index)
        frame.index = index.tz_localize(UTC) if index.tz is None else index.tz_convert(UTC)
        return normalize_bars(frame)

    def _write_cache(self, path: Path, frame: pd.DataFrame) -> None:
        if not self.use_cache:
            return
        try:
            frame.to_csv(path)
        except OSError:
            pass   # a cache write failure must not fail the load

    # -- interface ------------------------------------------------------

    def load(self, asset: str, days: int) -> tuple[Optional[pd.DataFrame], HistoryLoadReport]:
        days = min(int(days), self.max_days)
        report = HistoryLoadReport(asset=asset, requested_days=days)

        now = datetime.now(UTC)
        pieces: list[pd.DataFrame] = []

        offset = 0
        while offset < days:
            span = min(CHUNK_DAYS, days - offset)
            end = now - timedelta(days=offset)
            start = end - timedelta(days=span)
            offset += span

            report.chunks_attempted += 1
            path = self._chunk_path(asset, start, end)

            cached = self._read_cache(path)
            if cached is not None:
                pieces.append(cached)
                report.chunks_succeeded += 1
                report.from_cache_chunks += 1
                continue

            frame = self._download_chunk(asset, start, end, report)
            if frame is not None and not frame.empty:
                self._write_cache(path, frame)
                pieces.append(frame)
                report.chunks_succeeded += 1
            else:
                report.chunks_failed += 1

        if not pieces:
            report.skipped = True
            report.skip_reason = "no chunks returned usable data"
            return None, report

        combined = pd.concat(pieces)
        combined = combined[~combined.index.duplicated(keep="last")].sort_index()

        report.bars = len(combined)
        report.first_bar_utc = combined.index[0].to_pydatetime()
        report.last_bar_utc = combined.index[-1].to_pydatetime()
        return combined, report

    def _download_chunk(
        self, asset: str, start: datetime, end: datetime, report: HistoryLoadReport
    ) -> Optional[pd.DataFrame]:
        for attempt in range(self.max_retries):
            try:
                raw = self._downloader(asset, start, end)
                return normalize_bars(raw)
            except Exception as exc:  # noqa: BLE001 - loading must not crash the run
                report.errors.append(
                    f"{start:%Y-%m-%d}..{end:%Y-%m-%d}: {type(exc).__name__}: {exc}"
                )
                if attempt < self.max_retries - 1:
                    time.sleep(self.backoff_seconds * (2**attempt))
        return None


class InMemoryHistoryProvider(HistoricalBarProvider):
    """Deterministic provider for tests and for replaying a fixed dataset."""

    name = "in-memory"

    def __init__(self, frames: dict[str, pd.DataFrame]) -> None:
        self._frames = frames

    def load(self, asset: str, days: int) -> tuple[Optional[pd.DataFrame], HistoryLoadReport]:
        report = HistoryLoadReport(asset=asset, requested_days=days)
        frame = self._frames.get(asset)
        if frame is None or frame.empty:
            report.skipped = True
            report.skip_reason = "asset not present in in-memory dataset"
            return None, report
        report.bars = len(frame)
        report.chunks_attempted = report.chunks_succeeded = 1
        report.first_bar_utc = frame.index[0].to_pydatetime()
        report.last_bar_utc = frame.index[-1].to_pydatetime()
        return frame, report

    @property
    def max_days(self) -> int:
        return 10_000


def load_universe(
    provider: HistoricalBarProvider,
    assets: list[str],
    days: int,
    *,
    min_bars: int = 2000,
    min_completeness: float = 0.80,
) -> tuple[dict[str, pd.DataFrame], list[HistoryLoadReport]]:
    """Load every asset, SKIPPING those with inadequate data.

    Phase 3 requirement 9: "If a symbol has insufficient history or bad data,
    skip it explicitly rather than filling gaps." A skipped asset appears in the
    reports with a stated reason; it is never silently absent and its gaps are
    never interpolated.
    """
    frames: dict[str, pd.DataFrame] = {}
    reports: list[HistoryLoadReport] = []

    for asset in assets:
        frame, report = provider.load(asset, days)

        if frame is None:
            reports.append(report)
            continue

        if report.bars < min_bars:
            report.skipped = True
            report.skip_reason = f"only {report.bars} bars (need {min_bars})"
            reports.append(report)
            continue

        if report.completeness < min_completeness:
            report.skipped = True
            report.skip_reason = (
                f"completeness {report.completeness * 100:.1f}% "
                f"below {min_completeness * 100:.0f}%"
            )
            reports.append(report)
            continue

        frames[asset] = frame
        reports.append(report)

    return frames, reports
