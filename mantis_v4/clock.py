"""
MANTIS V4 — clock and timezone handling.

Phase 2 fix for audit findings A-4 (contract-rollover race) and G-6 (silent
timezone fallback).

Design rules enforced here:

1.  There is exactly ONE place that reads the wall clock: ``Clock.now_utc()``.
    Nothing below ``main()`` may call ``datetime.now()`` directly. A scan
    resolves the instant once and threads it through every downstream call, so
    a contract boundary can never fall *inside* a single scan's reasoning.

2.  All internal arithmetic is done in UTC. Local/Eastern time is a *display*
    concern only. This removes every DST wall-clock arithmetic hazard.

3.  If the contract timezone cannot be loaded, we FAIL LOUDLY. V3 silently fell
    back to the machine's local zone while continuing to print "ET", which is a
    lie on any machine not in US Eastern (audit G-6).

4.  Loop pacing uses ``time.monotonic()``, never ``time.time()`` (audit G-2,
    master prompt section 26K). ``time.time()`` can jump backwards on NTP
    correction; monotonic cannot.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc


class TimezoneUnavailable(RuntimeError):
    """Raised when the configured contract timezone cannot be loaded.

    We deliberately do NOT degrade to local time. A contract clock that is
    silently wrong is worse than a refusal to start.
    """


def load_timezone(tz_name: str) -> ZoneInfo:
    """Load a timezone, or raise with an actionable message.

    Fails loudly rather than falling back to local time (audit finding G-6).
    """
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise TimezoneUnavailable(
            f"Cannot load timezone {tz_name!r}: {type(exc).__name__}: {exc}\n"
            "MANTIS will not guess a timezone, because a wrong contract clock "
            "produces confidently wrong contract windows.\n"
            "Fix: python -m pip install tzdata"
        ) from exc


def assert_window_alignable(tz: ZoneInfo, window_minutes: int) -> None:
    """Verify that flooring in UTC is equivalent to flooring in local time.

    We compute contract windows by flooring the UTC instant (which has no DST
    discontinuities). That is only identical to flooring the *local* wall clock
    if the zone's UTC offset is a whole multiple of the window length.

    Every real-world IANA zone in modern use has an offset that is a multiple of
    15 minutes, so this holds for a 15-minute window. It is asserted rather than
    assumed, because a future config change (a 10-minute window, an exotic zone)
    could silently break the equivalence.
    """
    # Probe both a winter and a summer instant so DST offsets are both checked.
    probes = (
        datetime(2026, 1, 15, 12, 0, tzinfo=UTC),
        datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
    )
    for probe in probes:
        offset = probe.astimezone(tz).utcoffset()
        assert offset is not None
        offset_minutes = int(offset.total_seconds() // 60)
        if offset_minutes % window_minutes != 0:
            raise TimezoneUnavailable(
                f"Timezone {tz} has UTC offset {offset_minutes} minutes at "
                f"{probe.isoformat()}, which is not a multiple of the "
                f"{window_minutes}-minute contract window. UTC-based window "
                "flooring would not align with local wall-clock boundaries."
            )


@dataclass(frozen=True)
class Instant:
    """A single resolved point in time, captured once per scan.

    Carrying this object (rather than re-reading the clock) is what makes the
    rollover race in audit finding A-4 structurally impossible: every consumer
    in a scan sees the identical instant.

    ``monotonic`` is captured alongside ``utc`` so that elapsed-time measurement
    is immune to system clock adjustments.
    """

    utc: datetime
    monotonic: float

    def astimezone(self, tz: ZoneInfo) -> datetime:
        return self.utc.astimezone(tz)

    def isoformat(self) -> str:
        return self.utc.isoformat()

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return self.isoformat()


class Clock:
    """The single source of time for the whole system.

    Injectable so tests can drive contract boundaries deterministically without
    sleeping or monkey-patching ``datetime``.
    """

    def now_utc(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return time.monotonic()

    def capture(self) -> Instant:
        """Resolve the current instant exactly once."""
        return Instant(utc=self.now_utc(), monotonic=self.monotonic())

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


class FrozenClock(Clock):
    """Deterministic clock for tests and for the leakage-safe backtester.

    Phase 3 will drive the historical replayer through this so that
    "what could MANTIS have known at this exact timestamp" is enforced by
    construction rather than by discipline.
    """

    def __init__(self, start: datetime, monotonic_start: float = 0.0) -> None:
        if start.tzinfo is None:
            raise ValueError("FrozenClock requires a timezone-aware datetime")
        self._utc = start.astimezone(UTC)
        self._monotonic = monotonic_start

    def now_utc(self) -> datetime:
        return self._utc

    def monotonic(self) -> float:
        return self._monotonic

    def sleep(self, seconds: float) -> None:
        """Advance virtual time instead of blocking."""
        self.advance(seconds)

    def advance(self, seconds: float) -> None:
        self._utc = self._utc + timedelta(seconds=seconds)
        self._monotonic += seconds

    def set_to(self, moment: datetime) -> None:
        if moment.tzinfo is None:
            raise ValueError("FrozenClock requires a timezone-aware datetime")
        self._utc = moment.astimezone(UTC)


class LoopPacer:
    """Monotonic, drift-compensated loop pacing (master prompt section 26K).

    V3 used a flat ``time.sleep(POLL_SECONDS)`` after a variable-duration work
    phase, so the true scan period was ``10s + network time`` and drifted
    without bound (audit G-2). This sleeps only the remainder.

    It also reports overruns, so a slow provider becomes *visible* instead of
    silently stretching the scan interval.
    """

    def __init__(self, clock: Clock, interval_seconds: float) -> None:
        self._clock = clock
        self.interval_seconds = float(interval_seconds)
        self.last_work_seconds: float = 0.0
        self.overrun_count: int = 0

    def pace(self, loop_start_monotonic: float) -> float:
        """Sleep the remainder of the interval. Returns the work duration."""
        elapsed = self._clock.monotonic() - loop_start_monotonic
        self.last_work_seconds = elapsed
        remaining = self.interval_seconds - elapsed
        if remaining <= 0:
            self.overrun_count += 1
        self._clock.sleep(max(0.0, remaining))
        return elapsed


def format_seconds(total_seconds: float) -> str:
    """Render a duration as MM:SS (used for TIME LEFT columns)."""
    total_seconds = max(0.0, float(total_seconds))
    minutes = int(total_seconds // 60)
    seconds = int(total_seconds % 60)
    return f"{minutes:02d}:{seconds:02d}"


def parse_iso_utc(value: str) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp into an aware UTC datetime.

    A naive timestamp is REJECTED (returns None) rather than assumed to be UTC
    or local. Audit finding D-8: silently assuming a timezone is a wrong-answer
    path, not a crash path, and those are the dangerous ones.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)
