"""
MANTIS V4 — the leakage barrier (Phase 3 requirement 5).

This module is the single mechanism that makes look-ahead bias structurally
impossible rather than merely discouraged.

THE RULE

A 1-minute bar indexed at time ``t`` covers the half-open interval
``[t, t+60s)``. It is not COMPLETE — and therefore not knowable — until
``t + 60s``. So at scan instant ``T`` the visible set is:

    visible = { bar : bar.index + 60s <= T }

``HistoricalMarketView`` slices that set ONCE at construction and exposes only
the slice. A strategy cannot reach a future bar because the future bars are not
in the object it is handed. There is no discipline to violate.

SPOT PRICE AT SCAN TIME

    spot(T) = Close of the last visible bar

The bar ``[T-60s, T)`` closes exactly at ``T``, so when ``T`` falls on a minute
boundary this is precisely the price at ``T``. When ``T`` is mid-minute (the
15-second scan grid of master prompt section 26I) the most recent completed
close is up to 59 seconds old.

THIS IS A DELIBERATE, DOCUMENTED DIVERGENCE FROM LIVE BEHAVIOUR.

Live, yfinance hands MANTIS the in-progress bar whose Close tracks the current
price, so live sees a fresher number than the backtest does. The backtest is
therefore working with STALER information than production.

The direction of that bias matters and is stated plainly: the backtest
UNDERSTATES the information available live, so a rule's backtested performance
is a conservative estimate of its live performance, not an optimistic one. The
alternative — using the completed bar's Close as the price at a mid-bar instant
— would be genuine look-ahead, because that close is the price at the END of a
minute that has not finished yet. Audit finding D-4 is the live half of this
same issue.

THE REFERENCE IS THE ONE JUSTIFIED EXCEPTION

The proxy reference is the OPEN of the first bar at or after the contract
start. An opening price IS the instantaneous price at that timestamp, so it is
genuinely known at contract start rather than 60 seconds later. It is exposed
from ``bar.index`` onward — not from ``bar.index + 60s``.

That exception is narrow, explicit and tested. If the opening bar is missing
and the first available bar starts several minutes into the window, the
reference does not exist until that later bar's timestamp, and
``reference_available_at`` says so.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from ..clock import UTC
from ..contracts import ContractWindow

BAR_SECONDS = 60


@dataclass(frozen=True)
class ProxyReference:
    """The unverified settlement reference for one historical contract.

    Phase 3 requirement 1: this can NEVER be presented as verified Webull
    settlement data. ``verified`` is hard-coded False and there is no code path
    that sets it True — verification requires a real contract specification,
    which by definition cannot come from underlying bars.
    """

    price: float
    bar_utc: datetime
    rule: str = "OPEN_AT_OR_AFTER_START"
    source: str = "PROXY_WINDOW_OPEN"
    verified: bool = False

    def available_at(self, scan_utc: datetime) -> bool:
        """An opening price is known at its own timestamp, not 60s later."""
        return scan_utc.astimezone(UTC) >= self.bar_utc


def derive_proxy_reference(
    frame: pd.DataFrame, window: ContractWindow
) -> Optional[ProxyReference]:
    """Open of the first bar at or after the window start.

    Returns None when no bar falls inside the window at all. Mirrors the live
    ``UnderlyingProxyContractProvider`` exactly, including its refusal to accept
    a bar at or beyond the window end — using such a bar would silently anchor
    the contract to a price from a different window.
    """
    if frame is None or len(frame) == 0:
        return None

    subset = frame.loc[
        (frame.index >= window.start_utc) & (frame.index < window.end_utc)
    ]
    if subset.empty:
        return None

    bar_utc = subset.index[0].to_pydatetime()
    return ProxyReference(price=float(subset.iloc[0]["Open"]), bar_utc=bar_utc)


class HistoricalMarketView:
    """A strictly non-anticipating window onto historical bars.

    Construct one per scan. The frame it holds contains ONLY bars that were
    complete at ``scan_utc``; future bars are absent, not merely off-limits.
    """

    __slots__ = ("_full", "_visible_count", "_scan_utc", "_cutoff", "_asset")

    def __init__(
        self,
        frame: pd.DataFrame,
        scan_utc: datetime,
        asset: str = "",
        bar_seconds: int = BAR_SECONDS,
    ) -> None:
        scan_utc = scan_utc.astimezone(UTC)
        cutoff = scan_utc - timedelta(seconds=bar_seconds)
        self._asset = asset
        self._scan_utc = scan_utc
        self._cutoff = cutoff
        self._full = frame

        # Positional cutoff via binary search. The visible set is exactly
        # frame.iloc[:visible_count]; everything at or beyond that position is
        # in the future and is never reachable through this object.
        #
        # This replaces a boolean mask (`frame.index <= cutoff`), which copied
        # up to 40,000 rows on EVERY one of ~800,000 scans and made the run
        # unfinishable. The barrier is identical; only the cost changed.
        if frame is None or len(frame) == 0:
            self._visible_count = 0
        else:
            self._visible_count = int(
                frame.index.searchsorted(pd.Timestamp(cutoff), side="right")
            )

    # -- identity -------------------------------------------------------

    @property
    def asset(self) -> str:
        return self._asset

    @property
    def scan_utc(self) -> datetime:
        return self._scan_utc

    @property
    def cutoff_utc(self) -> datetime:
        """Latest bar timestamp this view may contain. Used by leakage tests."""
        return self._cutoff

    @property
    def frame(self) -> Optional[pd.DataFrame]:
        """Visible bars only. Materialised lazily; never mutate."""
        if self._full is None:
            return None
        return self._full.iloc[: self._visible_count]

    def __len__(self) -> int:
        return self._visible_count

    @property
    def is_empty(self) -> bool:
        return self._visible_count == 0

    # -- prices ---------------------------------------------------------

    @property
    def spot(self) -> Optional[float]:
        """Close of the last completed bar. O(1); see the module docstring."""
        if self.is_empty:
            return None
        return float(self._full["Close"].iat[self._visible_count - 1])

    @property
    def last_bar_utc(self) -> Optional[datetime]:
        if self.is_empty:
            return None
        return self._full.index[self._visible_count - 1].to_pydatetime()

    @property
    def spot_age_seconds(self) -> Optional[float]:
        """How stale the spot is at scan time. 0-59s on a 15-second scan grid."""
        if self.is_empty:
            return None
        close_time = self.last_bar_utc + timedelta(seconds=BAR_SECONDS)
        return max(0.0, (self._scan_utc - close_time).total_seconds())

    def tail(self, n: int) -> Optional[pd.DataFrame]:
        if self.is_empty:
            return None
        start = max(0, self._visible_count - n)
        return self._full.iloc[start : self._visible_count]

    # -- self-verification ----------------------------------------------

    def assert_no_lookahead(self) -> None:
        """Raise if this view somehow exposes a bar it should not.

        Cheap (O(1)) so it can run in the replayer's paranoid mode. It exists so
        that a future refactor cannot quietly widen the slice.
        """
        if self.is_empty:
            return
        newest = self.last_bar_utc
        if newest > self._cutoff:
            raise AssertionError(
                f"LOOK-AHEAD: view for {self._asset} at {self._scan_utc.isoformat()} "
                f"contains bar {newest.isoformat()} newer than cutoff "
                f"{self._cutoff.isoformat()}"
            )
        # The very next bar must be strictly beyond the cutoff, otherwise the
        # slice is too NARROW and we would be silently discarding known data.
        if self._visible_count < len(self._full):
            following = self._full.index[self._visible_count].to_pydatetime()
            if following <= self._cutoff:
                raise AssertionError(
                    f"SLICE TOO NARROW: bar {following.isoformat()} was complete "
                    f"at {self._scan_utc.isoformat()} but is not visible"
                )
