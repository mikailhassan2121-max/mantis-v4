"""
MANTIS V4 — provider interfaces.

Master prompt sections 2, 5, 26A and 26N. Everything that reaches outside the
process does so through one of these two interfaces, so that:

  * MANTIS still runs when any given feed is unavailable (section 26A);
  * no provider can crash the market loop (section 26N);
  * a better data source can be plugged in later without touching the model
    (the Phase 3 requirement that a longer history be pluggable);
  * per-provider health is always displayable.

Audit findings addressed: D-3 (no network timeout anywhere in V3) and the
general absence of retry/backoff/health reporting.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Optional, Sequence, TypeVar

from ..clock import Instant
from ..contracts import ContractQuote, ContractSpec, ContractWindow

T = TypeVar("T")


class HealthState(Enum):
    """Provider states. Ordered from best to worst for display purposes."""

    LIVE = "LIVE"
    DEGRADED = "DEGRADED"                # working, but retried or partial
    CACHED = "CACHED"                    # serving cache, upstream failing
    AUTH_NOT_CONFIGURED = "AUTH NOT CONFIGURED"
    SDK_NOT_INSTALLED = "SDK NOT INSTALLED"
    SCHEMA_UNVERIFIED = "SCHEMA UNVERIFIED"   # wired, but response shape unconfirmed
    UNAVAILABLE = "UNAVAILABLE"
    ERROR = "ERROR"

    @property
    def is_usable(self) -> bool:
        return self in (HealthState.LIVE, HealthState.DEGRADED, HealthState.CACHED)


@dataclass
class ProviderHealth:
    """Live health of one provider, for the status panel (section 26N)."""

    name: str
    state: HealthState = HealthState.UNAVAILABLE
    detail: str = ""
    last_success_utc: Optional[datetime] = None
    last_error: str = ""
    consecutive_failures: int = 0
    total_requests: int = 0
    total_failures: int = 0

    def record_success(self, when: datetime, detail: str = "", degraded: bool = False) -> None:
        self.state = HealthState.DEGRADED if degraded else HealthState.LIVE
        self.detail = detail
        self.last_success_utc = when
        self.consecutive_failures = 0
        self.total_requests += 1

    def record_failure(self, error: str, state: HealthState = HealthState.ERROR) -> None:
        self.state = state
        self.last_error = error
        self.detail = error
        self.consecutive_failures += 1
        self.total_requests += 1
        self.total_failures += 1

    def set_state(self, state: HealthState, detail: str = "") -> None:
        self.state = state
        self.detail = detail

    @property
    def display(self) -> str:
        if self.detail:
            return f"{self.state.value} - {self.detail}"
        return self.state.value


class ProviderError(RuntimeError):
    """Any provider-level failure. Callers must never let this escape a scan."""


class ProviderUnavailable(ProviderError):
    """The provider is structurally unable to serve this request.

    Distinct from a transient error: retrying will not help. Used, for example,
    when credentials are absent, or when a response schema has not yet been
    verified against real documentation.
    """


def retry_with_backoff(
    operation: Callable[[], T],
    *,
    attempts: int,
    backoff_seconds: float,
    health: Optional[ProviderHealth] = None,
    sleep: Callable[[float], None] = time.sleep,
    retry_on: Sequence[type[BaseException]] = (Exception,),
) -> T:
    """Run ``operation`` with exponential backoff (section 26N).

    ``ProviderUnavailable`` is never retried -- it is a structural condition,
    not a transient one, and retrying it just wastes the scan budget.

    Raises the final exception if every attempt fails. Callers are responsible
    for catching it; no provider failure may propagate into the market loop.
    """
    attempts = max(1, int(attempts))
    last_exc: Optional[BaseException] = None

    for attempt in range(attempts):
        try:
            return operation()
        except ProviderUnavailable:
            raise
        except retry_on as exc:  # noqa: PERF203 - retry is the point
            last_exc = exc
            if health is not None:
                health.last_error = f"{type(exc).__name__}: {exc}"
            if attempt < attempts - 1:
                sleep(backoff_seconds * (2**attempt))

    assert last_exc is not None
    raise ProviderError(f"{type(last_exc).__name__}: {last_exc}") from last_exc


# ---------------------------------------------------------------------------
# Market data (underlying prices)
# ---------------------------------------------------------------------------

@dataclass
class BarSet:
    """OHLCV bars for one asset, plus the provenance needed to judge them.

    ``frame`` is a pandas DataFrame indexed by tz-aware UTC timestamps with
    columns Open/High/Low/Close/Volume. The index is bar START time.

    ``last_bar_is_partial`` matters more than it looks: audit finding D-4
    showed V3 silently mixed a partial in-progress bar's High/Low/Volume with
    completed bars, giving ``mom1`` a once-per-minute sawtooth bias and making
    live behaviour irreproducible in a backtest. Carrying the flag lets Phase 3
    reproduce live conditions exactly instead of guessing.
    """

    asset: str
    frame: Any                       # pandas.DataFrame
    source: str
    fetched_at_utc: datetime
    last_bar_is_partial: bool = True
    from_cache: bool = False

    def __len__(self) -> int:
        return 0 if self.frame is None else len(self.frame)

    @property
    def is_empty(self) -> bool:
        return self.frame is None or len(self.frame) == 0

    @property
    def last_timestamp(self) -> Optional[datetime]:
        if self.is_empty:
            return None
        return self.frame.index[-1].to_pydatetime()

    @property
    def last_close(self) -> Optional[float]:
        if self.is_empty:
            return None
        return float(self.frame["Close"].iloc[-1])


class MarketDataProvider(ABC):
    """Underlying price bars. yfinance is one implementation, not the contract.

    Phase 3 will add a higher-history provider behind this same interface; the
    modelling code must not need to change when that happens.
    """

    name: str = "market-data"

    def __init__(self) -> None:
        self.health = ProviderHealth(name=self.name)

    @abstractmethod
    def get_bars(self, asset: str, instant: Instant) -> Optional[BarSet]:
        """Return recent bars, or None if unavailable. Must never raise."""

    def supported_assets(self) -> Optional[list[str]]:
        return None


# ---------------------------------------------------------------------------
# Event contracts (strike, settlement rule, quotes)
# ---------------------------------------------------------------------------

class EventContractProvider(ABC):
    """Contract specifications and quotes (master prompt section 26A).

    Implementations must degrade gracefully: an unavailable provider returns
    None and reports its health. It never raises into the market loop, and it
    never invents a strike, quote, expiration, or implied probability.
    """

    name: str = "event-contract"
    priority: int = 100                  # lower = preferred

    def __init__(self) -> None:
        self.health = ProviderHealth(name=self.name)

    @abstractmethod
    def get_contract_spec(
        self,
        asset: str,
        window: ContractWindow,
        instant: Instant,
    ) -> Optional[ContractSpec]:
        """Full specification for one asset/window, or None."""

    def get_quotes(
        self,
        asset: str,
        window: ContractWindow,
        instant: Instant,
    ) -> Optional[ContractQuote]:
        """Current two-sided market. Default: whatever the spec carries."""
        spec = self.get_contract_spec(asset, window, instant)
        return None if spec is None else spec.quote

    def get_active_contracts(
        self,
        window: ContractWindow,
        instant: Instant,
        assets: Sequence[str],
    ) -> dict[str, ContractSpec]:
        """Specs for every asset this provider can serve in ``window``."""
        found: dict[str, ContractSpec] = {}
        for asset in assets:
            try:
                spec = self.get_contract_spec(asset, window, instant)
            except ProviderError:
                continue
            if spec is not None:
                found[asset] = spec
        return found

    def get_reference_price(
        self,
        asset: str,
        window: ContractWindow,
        instant: Instant,
    ) -> Optional[float]:
        spec = self.get_contract_spec(asset, window, instant)
        return None if spec is None else spec.reference_price

    def get_resolution_time(self, window: ContractWindow) -> datetime:
        """Resolution instant. Only an official provider may override this."""
        return window.end_utc
