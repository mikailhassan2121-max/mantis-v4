"""
MANTIS V4 — exact contract semantics.

This module is the authority on "which contract are we in, and what does it
settle against". Everything else consumes these objects; nothing else is
permitted to infer a contract window.

Phase 2 fixes for audit findings:

  A-1  contract_id collided during the DST fall-back hour because it was built
       from local wall-clock strftime alone. It now carries the UTC epoch and
       is provably unique.
  A-4  windows are computed once per scan from a single captured Instant.
  E-1  the settlement reference is now an explicit, typed, provenance-tagged
       value. A proxy reference can never be mistaken for a verified strike,
       because the type system carries the distinction.
  E-2  contract economics live in an optional Quote. Absent quotes mean absent
       economics -- never a placeholder number.

Master prompt section 0 defines the windows as hard ET clock buckets:
    :00 -> :15,  :15 -> :30,  :30 -> :45,  :45 -> next :00

Windows are floored in UTC, not in local time. Because every real IANA zone's
UTC offset is a whole multiple of 15 minutes, flooring in UTC yields exactly
the same instants as flooring the local wall clock -- but without any DST
discontinuity to reason about. ``clock.assert_window_alignable`` verifies this
equivalence at startup rather than assuming it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional
from zoneinfo import ZoneInfo

from .clock import UTC, Instant

DEFAULT_WINDOW_MINUTES = 15


# ---------------------------------------------------------------------------
# Provenance and settlement typing
# ---------------------------------------------------------------------------

class ReferenceSource(Enum):
    """Where the settlement reference/strike came from.

    The ordering of the enum reflects trust. Only ``OFFICIAL_PROVIDER`` may be
    described to the user as a verified contract strike.
    """

    OFFICIAL_PROVIDER = "OFFICIAL_PROVIDER"   # authenticated event-contract API
    MANUAL_LOCAL = "MANUAL_LOCAL"             # user-supplied local JSON/CSV
    PROXY_WINDOW_OPEN = "PROXY_WINDOW_OPEN"   # first 1m Open -- UNVERIFIED
    UNAVAILABLE = "UNAVAILABLE"               # no reference at all

    @property
    def is_verified(self) -> bool:
        """True only for a reference obtained from the contract specification.

        MANUAL_LOCAL is deliberately NOT verified: it is whatever the user
        typed, and MANTIS has no way to confirm it against the venue.
        """
        return self is ReferenceSource.OFFICIAL_PROVIDER

    @property
    def display(self) -> str:
        return {
            ReferenceSource.OFFICIAL_PROVIDER: "OFFICIAL",
            ReferenceSource.MANUAL_LOCAL: "MANUAL / USER-SUPPLIED",
            ReferenceSource.PROXY_WINDOW_OPEN: "PROXY / UNVERIFIED",
            ReferenceSource.UNAVAILABLE: "UNAVAILABLE",
        }[self]


class SettlementRule(Enum):
    """How the contract decides YES vs NO at resolution.

    UNKNOWN is the honest default. Master prompt section 26B: "The contract's
    actual settlement rule is the authority." Until a real specification is
    read, MANTIS uses PROXY_TERMINAL_ABOVE_REFERENCE for *research* labelling
    and flags every result as proxy-derived.
    """

    UNKNOWN = "UNKNOWN"
    # Verified rules (only ever set from an official contract specification).
    TERMINAL_ABOVE_REFERENCE = "TERMINAL_ABOVE_REFERENCE"
    TERMINAL_AT_OR_ABOVE_REFERENCE = "TERMINAL_AT_OR_ABOVE_REFERENCE"
    # Research-only rule used when settling against a proxy reference.
    PROXY_TERMINAL_ABOVE_REFERENCE = "PROXY_TERMINAL_ABOVE_REFERENCE"

    @property
    def is_verified(self) -> bool:
        return self in (
            SettlementRule.TERMINAL_ABOVE_REFERENCE,
            SettlementRule.TERMINAL_AT_OR_ABOVE_REFERENCE,
        )

    def settle(self, terminal_price: float, reference: float) -> Optional[bool]:
        """Return True if YES wins, False if NO wins, None if undecidable.

        Tie handling is explicit rather than incidental. V3 never had to make
        this decision because it never settled anything (audit A-2).
        """
        if self is SettlementRule.UNKNOWN:
            return None
        if self is SettlementRule.TERMINAL_AT_OR_ABOVE_REFERENCE:
            return terminal_price >= reference
        # Both strict-above variants (verified and proxy) behave identically;
        # they differ only in what we are allowed to CLAIM about the result.
        return terminal_price > reference


class ContractStatus(Enum):
    """Lifecycle of a recorded contract. See recording.py."""

    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    UNRESOLVED_NO_DATA = "UNRESOLVED_NO_DATA"


# ---------------------------------------------------------------------------
# ContractWindow -- the hard clock bucket
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ContractWindow:
    """One immutable 15-minute clock bucket.

    Frozen because a window must never be mutated mid-scan. Two windows are
    equal iff they describe the same absolute interval.
    """

    start_utc: datetime
    end_utc: datetime
    tz_name: str

    def __post_init__(self) -> None:
        if self.start_utc.tzinfo is None or self.end_utc.tzinfo is None:
            raise ValueError("ContractWindow requires timezone-aware datetimes")
        if self.end_utc <= self.start_utc:
            raise ValueError("ContractWindow end must be after start")

    # -- construction -------------------------------------------------------

    @classmethod
    def for_instant(
        cls,
        instant: Instant | datetime,
        tz: ZoneInfo,
        window_minutes: int = DEFAULT_WINDOW_MINUTES,
    ) -> "ContractWindow":
        """The window containing ``instant``.

        Boundary convention: the window is half-open, ``[start, end)``. At
        exactly :15:00 you are in the NEW window, never the old one. This is
        what makes 11:14:59 and 11:15:00 land in different contracts.
        """
        moment = instant.utc if isinstance(instant, Instant) else instant
        if moment.tzinfo is None:
            raise ValueError("ContractWindow.for_instant requires an aware datetime")
        moment = moment.astimezone(UTC)

        # Floor in UTC. No DST discontinuity exists in UTC, so this is exact.
        epoch_seconds = int(moment.timestamp())
        window_seconds = window_minutes * 60
        start_epoch = (epoch_seconds // window_seconds) * window_seconds

        start = datetime.fromtimestamp(start_epoch, tz=UTC)
        end = start + timedelta(minutes=window_minutes)
        return cls(start_utc=start, end_utc=end, tz_name=str(tz))

    def next_window(self) -> "ContractWindow":
        length = self.end_utc - self.start_utc
        return ContractWindow(
            start_utc=self.end_utc,
            end_utc=self.end_utc + length,
            tz_name=self.tz_name,
        )

    def previous_window(self) -> "ContractWindow":
        length = self.end_utc - self.start_utc
        return ContractWindow(
            start_utc=self.start_utc - length,
            end_utc=self.start_utc,
            tz_name=self.tz_name,
        )

    # -- identity -----------------------------------------------------------

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.tz_name)

    @property
    def start_local(self) -> datetime:
        return self.start_utc.astimezone(self.tz)

    @property
    def end_local(self) -> datetime:
        return self.end_utc.astimezone(self.tz)

    @property
    def contract_id(self) -> str:
        """Globally unique, human-readable window identifier.

        Audit fix A-1. V3 used ``%Y%m%d-%H%M_%H%M`` of local time, which
        collides during the DST fall-back hour: 01:15-01:30 EDT and
        01:15-01:30 EST produced the same string on the same date.

        The trailing UTC epoch makes collision impossible while the leading
        local wall-clock portion keeps the ID readable at a glance.
        """
        start = self.start_local
        end = self.end_local
        return (
            f"{start:%Y%m%dT%H%M}-{end:%H%M}-{int(self.start_utc.timestamp())}"
        )

    def key_for(self, asset: str) -> str:
        """Per-asset primary key: one contract row per (window, asset)."""
        return f"{self.contract_id}|{asset}"

    @property
    def label(self) -> str:
        """Human display, e.g. '11:00 -> 11:15 PM ET'."""
        start = self.start_local
        end = self.end_local
        return f"{start:%I:%M} -> {end:%I:%M %p} {start:%Z}"

    # -- timing -------------------------------------------------------------

    @property
    def total_seconds(self) -> float:
        return (self.end_utc - self.start_utc).total_seconds()

    def contains(self, instant: Instant | datetime) -> bool:
        moment = instant.utc if isinstance(instant, Instant) else instant
        moment = moment.astimezone(UTC)
        return self.start_utc <= moment < self.end_utc

    def seconds_remaining(self, instant: Instant | datetime) -> float:
        moment = instant.utc if isinstance(instant, Instant) else instant
        return max(0.0, (self.end_utc - moment.astimezone(UTC)).total_seconds())

    def elapsed_seconds(self, instant: Instant | datetime) -> float:
        moment = instant.utc if isinstance(instant, Instant) else instant
        return max(0.0, (moment.astimezone(UTC) - self.start_utc).total_seconds())

    def elapsed_fraction(self, instant: Instant | datetime) -> float:
        return min(1.0, self.elapsed_seconds(instant) / self.total_seconds)


# ---------------------------------------------------------------------------
# Quotes and specifications
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ContractQuote:
    """Executable two-sided market for a binary event contract.

    All prices are in dollars per contract, in [0, 1]. A contract that settles
    YES pays 1.00; a contract that settles NO pays 0.00.

    Every field is Optional because a partial quote is a real thing and must
    not be papered over with a default. ``None`` means "not known" and MUST
    render as N/A, never as 0.
    """

    yes_bid: Optional[float] = None
    yes_ask: Optional[float] = None
    no_bid: Optional[float] = None
    no_ask: Optional[float] = None
    quote_timestamp: Optional[datetime] = None
    source: str = "UNAVAILABLE"

    @property
    def is_two_sided(self) -> bool:
        return self.yes_ask is not None and self.no_ask is not None

    def age_seconds(self, instant: Instant | datetime) -> Optional[float]:
        if self.quote_timestamp is None:
            return None
        moment = instant.utc if isinstance(instant, Instant) else instant
        return max(0.0, (moment.astimezone(UTC) - self.quote_timestamp).total_seconds())

    def is_fresh(self, instant: Instant | datetime, max_age_seconds: float) -> bool:
        age = self.age_seconds(instant)
        if age is None:
            return False
        return age <= max_age_seconds

    @property
    def yes_spread(self) -> Optional[float]:
        if self.yes_bid is None or self.yes_ask is None:
            return None
        return self.yes_ask - self.yes_bid

    @property
    def no_spread(self) -> Optional[float]:
        if self.no_bid is None or self.no_ask is None:
            return None
        return self.no_ask - self.no_bid

    def is_sane(self) -> bool:
        """Basic arbitrage/validity checks on a quote before it is trusted.

        A quote failing this must not be used for EV. We check the structure
        only -- we do NOT invent or repair missing sides.
        """
        prices = [self.yes_bid, self.yes_ask, self.no_bid, self.no_ask]
        for price in prices:
            if price is None:
                continue
            if not isinstance(price, (int, float)):
                return False
            if price != price:  # NaN
                return False
            if not (0.0 <= float(price) <= 1.0):
                return False
        if self.yes_bid is not None and self.yes_ask is not None:
            if self.yes_bid > self.yes_ask:
                return False
        if self.no_bid is not None and self.no_ask is not None:
            if self.no_bid > self.no_ask:
                return False
        return True


@dataclass
class ContractSpec:
    """Everything MANTIS knows about one tradeable contract.

    Satisfies master prompt section 2's required field list, plus explicit
    provenance flags so that the UI can never present a proxy as a strike.
    """

    window: ContractWindow
    underlying: str

    reference_price: Optional[float] = None
    reference_source: ReferenceSource = ReferenceSource.UNAVAILABLE
    settlement_rule: SettlementRule = SettlementRule.UNKNOWN

    quote: ContractQuote = field(default_factory=ContractQuote)

    provider_name: str = "none"
    venue_contract_id: Optional[str] = None   # the venue's own identifier
    venue_symbol: Optional[str] = None
    notes: str = ""

    # -- identity -----------------------------------------------------------

    @property
    def contract_id(self) -> str:
        return self.window.contract_id

    @property
    def key(self) -> str:
        return self.window.key_for(self.underlying)

    @property
    def start_time_et(self) -> datetime:
        return self.window.start_local

    @property
    def resolution_time_et(self) -> datetime:
        return self.window.end_local

    # -- capability flags ---------------------------------------------------

    @property
    def has_reference(self) -> bool:
        return (
            self.reference_price is not None
            and self.reference_source is not ReferenceSource.UNAVAILABLE
        )

    @property
    def reference_is_verified(self) -> bool:
        """True only when BOTH the reference and the settlement rule are
        verified from a real contract specification.

        A verified price settled by an unknown rule is still not a verified
        contract, so both must hold.
        """
        return (
            self.has_reference
            and self.reference_source.is_verified
            and self.settlement_rule.is_verified
        )

    def quote_is_fresh(self, instant: Instant | datetime, max_age_seconds: float) -> bool:
        return self.quote.is_fresh(instant, max_age_seconds)

    def economics_available(
        self,
        instant: Instant | datetime,
        max_quote_age_seconds: float,
    ) -> bool:
        """Whether EV arithmetic is permitted at all.

        Master prompt section 3: never fake these values. EV requires a
        verified reference, a sane two-sided quote, and a fresh quote. Failing
        any one of those disables the EV engine rather than degrading it.
        """
        return (
            self.reference_is_verified
            and self.quote.is_two_sided
            and self.quote.is_sane()
            and self.quote_is_fresh(instant, max_quote_age_seconds)
        )

    # -- display ------------------------------------------------------------

    def reference_display(self) -> str:
        if not self.has_reference:
            return "REFERENCE: UNAVAILABLE"
        if self.reference_is_verified:
            return f"REFERENCE: {self.reference_price:.6g} (OFFICIAL)"
        return f"REFERENCE: {self.reference_price:.6g} (PROXY / UNVERIFIED)"

    def settle(self, terminal_price: float) -> Optional[bool]:
        """Resolve the contract. None when it cannot be decided honestly."""
        if self.reference_price is None:
            return None
        return self.settlement_rule.settle(terminal_price, self.reference_price)
