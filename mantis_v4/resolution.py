"""
MANTIS V4 — contract resolution engine.

This is the enforcement half of the standing instruction:

    Do not allow any version of MANTIS to resolve a contract without logging
    its outcome.

``ContractRecorder`` provides durable storage; this module guarantees that
every stored contract eventually reaches a terminal status. It is called from
three places, and the redundancy is intentional:

  1. at STARTUP -- recovers contracts orphaned by a crash, a Ctrl-C, or a
     machine sleeping through a boundary (audit C-4: V3 kept all state in
     module globals and lost a live position entirely on restart);
  2. on every SCAN -- resolves windows as they mature;
  3. on ROLLOVER -- the moment a boundary is crossed.

Any one of the three would usually suffice. All three run because the failure
mode they prevent -- a contract expiring unrecorded -- is precisely the defect
that left V3 with a database of features and no labels.

TERMINAL PRICE CONVENTION

The terminal price is the Open of the first 1-minute bar at or after the
resolution instant -- the same rule used for the window's opening reference.
Using one rule for both ends keeps the measurement symmetric, which matters
because the *difference* between them is the label being learned. An asymmetric
convention (open vs close) would inject a systematic half-bar bias into every
label in the dataset.

Fallback is the Close of the last bar at or before resolution, accepted only
when it is close enough to the boundary to be meaningful. If neither is
available the contract becomes UNRESOLVED_NO_DATA with a stated reason. It is
never guessed.

AUDIT UNKNOWN-3 REMAINS OPEN. When the reference is a proxy, the settled
outcome is a research label derived from Yahoo 1-minute bars -- not the venue's
settlement. Every such row carries reference_verified = 0 so that Phase 3 can
partition on it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Optional

from .clock import UTC, Instant, parse_iso_utc
from .contracts import SettlementRule
from .providers.base import BarSet
from .providers.market_data import price_at_or_after, price_at_or_before
from .recording import ContractRecorder

BarLookup = Callable[[str], Optional[BarSet]]

# How far past the resolution instant a bar may sit and still be accepted as
# the terminal observation. Beyond this the gap is too large to call it the
# price "at" resolution.
DEFAULT_TERMINAL_TOLERANCE_SECONDS = 180.0


@dataclass
class TerminalObservation:
    price: float
    source: str
    bar_utc: datetime
    lag_seconds: float


def find_terminal_price(
    frame,
    resolution_utc: datetime,
    *,
    tolerance_seconds: float = DEFAULT_TERMINAL_TOLERANCE_SECONDS,
) -> Optional[TerminalObservation]:
    """Resolve the underlying price at the contract's resolution instant.

    Returns None rather than an approximation when no bar is close enough.
    """
    if frame is None or len(frame) == 0:
        return None

    resolution_utc = resolution_utc.astimezone(UTC)

    forward = price_at_or_after(frame, resolution_utc)
    if forward is not None:
        price, rule, bar_time = forward
        lag = (bar_time - resolution_utc).total_seconds()
        if lag <= tolerance_seconds:
            return TerminalObservation(price, rule, bar_time, lag)

    backward = price_at_or_before(frame, resolution_utc)
    if backward is not None:
        price, rule, bar_time = backward
        lag = (resolution_utc - bar_time).total_seconds()
        if lag <= tolerance_seconds:
            return TerminalObservation(price, rule, bar_time, -lag)

    return None


class ResolutionEngine:
    """Drives every OPEN contract to a terminal status."""

    def __init__(
        self,
        recorder: ContractRecorder,
        bar_lookup: BarLookup,
        *,
        grace_seconds: float = 120.0,
        max_attempts: int = 20,
        terminal_tolerance_seconds: float = DEFAULT_TERMINAL_TOLERANCE_SECONDS,
    ) -> None:
        self.recorder = recorder
        self._bar_lookup = bar_lookup
        self.grace_seconds = grace_seconds
        self.max_attempts = max_attempts
        self.terminal_tolerance_seconds = terminal_tolerance_seconds

    # ------------------------------------------------------------------

    def resolve_due(self, instant: Instant) -> list[dict]:
        """Resolve every contract whose window has closed. Never raises.

        Returns the rows that reached a terminal status in this pass, so the
        caller can announce them.
        """
        finished: list[dict] = []

        for row in self.recorder.due_contracts(instant, self.grace_seconds):
            try:
                result = self._resolve_one(row, instant)
            except Exception as exc:  # noqa: BLE001 - resolution must never crash the loop
                self.recorder.bump_resolution_attempt(
                    row["key"], f"{type(exc).__name__}: {exc}"
                )
                continue
            if result is not None:
                finished.append(result)

        return finished

    def _resolve_one(self, row, instant: Instant) -> Optional[dict]:
        key = row["key"]
        asset = row["asset"]

        reference = row["reference_price"]
        rule_text = row["settlement_rule"] or SettlementRule.UNKNOWN.value

        end_utc = parse_iso_utc(row["window_end_utc"])
        if end_utc is None:
            return self.recorder.mark_unresolvable(
                key, instant, "window_end_utc unparseable"
            )

        if reference is None:
            # No reference was ever established -- there is nothing to settle
            # against. Recorded permanently so Phase 3 can count it.
            return self.recorder.mark_unresolvable(
                key, instant, "no settlement reference was established"
            )

        try:
            rule = SettlementRule(rule_text)
        except ValueError:
            rule = SettlementRule.UNKNOWN

        if rule is SettlementRule.UNKNOWN:
            return self.recorder.mark_unresolvable(
                key, instant, "settlement rule unknown"
            )

        bars = self._bar_lookup(asset)
        if bars is None or bars.is_empty:
            return self._retry_or_give_up(key, instant, "no bars available")

        observation = find_terminal_price(
            bars.frame, end_utc, tolerance_seconds=self.terminal_tolerance_seconds
        )
        if observation is None:
            return self._retry_or_give_up(
                key, instant, "no bar within tolerance of resolution instant"
            )

        outcome_yes = rule.settle(observation.price, float(reference))
        if outcome_yes is None:
            return self.recorder.mark_unresolvable(
                key, instant, f"rule {rule.value} could not settle"
            )

        return self.recorder.mark_resolved(
            key,
            instant,
            terminal_price=observation.price,
            terminal_source=f"{observation.source}(lag={observation.lag_seconds:+.0f}s)",
            terminal_bar_utc=observation.bar_utc,
            outcome_yes=outcome_yes,
        )

    def _retry_or_give_up(self, key: str, instant: Instant, reason: str) -> Optional[dict]:
        """Retry a resolvable-looking contract, but bound the retries.

        Without a bound, a contract whose data never arrives would be retried
        forever and would sit in OPEN indefinitely -- which is exactly the
        "silently never resolved" state this module exists to prevent.
        """
        attempts = self.recorder.bump_resolution_attempt(key, reason)
        if attempts >= self.max_attempts:
            return self.recorder.mark_unresolvable(
                key,
                instant,
                f"{reason} (gave up after {attempts} attempts)",
            )
        return None

    # ------------------------------------------------------------------

    def recover_on_startup(self, instant: Instant) -> dict:
        """Resolve contracts orphaned by a previous run (audit C-4).

        Returns a small report so startup can state plainly what it found,
        rather than quietly cleaning up.
        """
        open_before = self.recorder.open_contracts()
        stale = [
            row for row in open_before
            if (parse_iso_utc(row["window_end_utc"]) or instant.utc) <= instant.utc
        ]
        resolved = self.resolve_due(instant)
        return {
            "open_at_startup": len(open_before),
            "expired_at_startup": len(stale),
            "resolved_now": len(resolved),
            "still_open": len(self.recorder.open_contracts()),
        }
