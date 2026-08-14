"""
Contract-semantics tests (master prompt section 22).

Covers the exact boundary list the master prompt requires, the DST transitions,
and the contract_id collision that Phase 1 confirmed in V3 (audit finding A-1).
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from mantis_v4.clock import UTC, Instant, assert_window_alignable, parse_iso_utc
from mantis_v4.contracts import (
    ContractQuote,
    ContractSpec,
    ContractWindow,
    ReferenceSource,
    SettlementRule,
)

ET = ZoneInfo("America/New_York")


def et(year, month, day, hour, minute, second=0, fold=0) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=ET, fold=fold)


def window_at(moment: datetime) -> ContractWindow:
    return ContractWindow.for_instant(moment, ET, 15)


class TestWindowBoundaries(unittest.TestCase):
    """The exact ten boundary cases listed in master prompt section 22."""

    def test_required_boundary_cases(self):
        cases = [
            # (time, expected window start minute, expected window end minute)
            ((10, 59, 59), (10, 45), (11, 0)),
            ((11, 0, 0), (11, 0), (11, 15)),
            ((11, 14, 59), (11, 0), (11, 15)),
            ((11, 15, 0), (11, 15), (11, 30)),
            ((11, 29, 59), (11, 15), (11, 30)),
            ((11, 30, 0), (11, 30), (11, 45)),
            ((11, 44, 59), (11, 30), (11, 45)),
            ((11, 45, 0), (11, 45), (12, 0)),
            ((11, 59, 59), (11, 45), (12, 0)),
            ((12, 0, 0), (12, 0), (12, 15)),
        ]
        for (h, m, s), (sh, sm), (eh, em) in cases:
            with self.subTest(time=f"{h:02d}:{m:02d}:{s:02d}"):
                moment = et(2026, 8, 13, h, m, s)
                window = window_at(moment)
                self.assertEqual((window.start_local.hour, window.start_local.minute), (sh, sm))
                self.assertEqual((window.end_local.hour, window.end_local.minute), (eh, em))
                self.assertTrue(window.contains(moment))

    def test_boundary_instant_belongs_to_new_window(self):
        """11:14:59 and 11:15:00 must be different contracts."""
        before = window_at(et(2026, 8, 13, 11, 14, 59))
        at = window_at(et(2026, 8, 13, 11, 15, 0))
        self.assertNotEqual(before.contract_id, at.contract_id)
        self.assertEqual(before.end_utc, at.start_utc)

    def test_window_is_half_open(self):
        window = window_at(et(2026, 8, 13, 11, 0, 0))
        self.assertTrue(window.contains(et(2026, 8, 13, 11, 0, 0)))
        self.assertTrue(window.contains(et(2026, 8, 13, 11, 14, 59)))
        self.assertFalse(window.contains(et(2026, 8, 13, 11, 15, 0)))

    def test_seconds_remaining(self):
        window = window_at(et(2026, 8, 13, 11, 0, 0))
        self.assertAlmostEqual(window.seconds_remaining(et(2026, 8, 13, 11, 0, 0)), 900.0)
        self.assertAlmostEqual(window.seconds_remaining(et(2026, 8, 13, 11, 14, 59)), 1.0)
        self.assertAlmostEqual(window.seconds_remaining(et(2026, 8, 13, 11, 20, 0)), 0.0)

    def test_elapsed_fraction(self):
        window = window_at(et(2026, 8, 13, 11, 0, 0))
        self.assertAlmostEqual(window.elapsed_fraction(et(2026, 8, 13, 11, 0, 0)), 0.0)
        self.assertAlmostEqual(window.elapsed_fraction(et(2026, 8, 13, 11, 7, 30)), 0.5)
        self.assertAlmostEqual(window.elapsed_fraction(et(2026, 8, 13, 11, 30, 0)), 1.0)

    def test_next_and_previous_are_adjacent(self):
        window = window_at(et(2026, 8, 13, 11, 0, 0))
        self.assertEqual(window.next_window().start_utc, window.end_utc)
        self.assertEqual(window.previous_window().end_utc, window.start_utc)

    def test_all_windows_in_a_day_are_unique_and_contiguous(self):
        start = datetime(2026, 8, 13, 0, 0, tzinfo=UTC)
        seen = set()
        previous = None
        for step in range(96):   # 24h of 15-minute windows
            window = ContractWindow.for_instant(start + timedelta(minutes=15 * step), ET, 15)
            self.assertNotIn(window.contract_id, seen)
            seen.add(window.contract_id)
            if previous is not None:
                self.assertEqual(window.start_utc, previous.end_utc)
            previous = window
        self.assertEqual(len(seen), 96)


class TestDaylightSaving(unittest.TestCase):
    """Audit finding A-1: V3's contract_id collided during the fall-back hour."""

    def test_fall_back_hour_produces_unique_contract_ids(self):
        # 2026-11-01: 02:00 EDT -> 01:00 EST. The 01:00-02:00 hour occurs twice.
        edt = et(2026, 11, 1, 1, 20, 0, fold=0)
        est = et(2026, 11, 1, 1, 20, 0, fold=1)

        self.assertEqual(edt.tzname(), "EDT")
        self.assertEqual(est.tzname(), "EST")

        first = window_at(edt)
        second = window_at(est)

        # Same local wall clock...
        self.assertEqual(first.start_local.strftime("%H:%M"), second.start_local.strftime("%H:%M"))
        # ...different absolute instants...
        self.assertNotEqual(first.start_utc, second.start_utc)
        # ...therefore MUST be different contracts. V3 produced identical IDs.
        self.assertNotEqual(first.contract_id, second.contract_id)

    def test_fall_back_hour_windows_are_all_distinct(self):
        ids = set()
        for fold in (0, 1):
            for minute in (0, 15, 30, 45):
                window = window_at(et(2026, 11, 1, 1, minute, 0, fold=fold))
                ids.add(window.contract_id)
        self.assertEqual(len(ids), 8, "fall-back hour must yield 8 distinct contracts")

    def test_fall_back_seconds_remaining_stays_sane(self):
        for fold in (0, 1):
            moment = et(2026, 11, 1, 1, 5, 0, fold=fold)
            window = window_at(moment)
            remaining = window.seconds_remaining(moment)
            self.assertGreater(remaining, 0.0)
            self.assertLessEqual(remaining, 900.0)

    def test_spring_forward_gap(self):
        # 2026-03-08: 02:00 EST -> 03:00 EDT. 02:00-03:00 local never happens.
        moment = et(2026, 3, 8, 1, 50, 0)
        window = window_at(moment)
        self.assertAlmostEqual(window.seconds_remaining(moment), 600.0)
        # The window after 01:45-02:00 EST must begin at 03:00 EDT local.
        following = window.next_window()
        self.assertEqual(following.start_local.hour, 3)
        self.assertEqual(following.start_local.minute, 0)

    def test_spring_forward_windows_contiguous_in_utc(self):
        start = datetime(2026, 3, 8, 5, 0, tzinfo=UTC)   # 00:00 EST
        previous = None
        for step in range(24):
            window = ContractWindow.for_instant(start + timedelta(minutes=15 * step), ET, 15)
            if previous is not None:
                self.assertEqual(window.start_utc, previous.end_utc)
            previous = window

    def test_window_alignment_assertion(self):
        assert_window_alignable(ET, 15)
        assert_window_alignable(ZoneInfo("Asia/Kolkata"), 15)   # +05:30 is 22*15m
        assert_window_alignable(ZoneInfo("UTC"), 15)


class TestSettlement(unittest.TestCase):
    def test_strict_above(self):
        rule = SettlementRule.TERMINAL_ABOVE_REFERENCE
        self.assertTrue(rule.settle(100.01, 100.0))
        self.assertFalse(rule.settle(100.0, 100.0))     # tie -> NO
        self.assertFalse(rule.settle(99.99, 100.0))

    def test_at_or_above(self):
        rule = SettlementRule.TERMINAL_AT_OR_ABOVE_REFERENCE
        self.assertTrue(rule.settle(100.01, 100.0))
        self.assertTrue(rule.settle(100.0, 100.0))      # tie -> YES
        self.assertFalse(rule.settle(99.99, 100.0))

    def test_unknown_rule_cannot_settle(self):
        self.assertIsNone(SettlementRule.UNKNOWN.settle(100.0, 99.0))

    def test_proxy_rule_settles_but_is_not_verified(self):
        rule = SettlementRule.PROXY_TERMINAL_ABOVE_REFERENCE
        self.assertTrue(rule.settle(100.01, 100.0))
        self.assertFalse(rule.is_verified)

    def test_only_official_source_is_verified(self):
        self.assertTrue(ReferenceSource.OFFICIAL_PROVIDER.is_verified)
        self.assertFalse(ReferenceSource.MANUAL_LOCAL.is_verified)
        self.assertFalse(ReferenceSource.PROXY_WINDOW_OPEN.is_verified)
        self.assertFalse(ReferenceSource.UNAVAILABLE.is_verified)


class TestQuotes(unittest.TestCase):
    def setUp(self):
        self.window = window_at(et(2026, 8, 13, 11, 0, 0))
        self.now = Instant(utc=datetime(2026, 8, 13, 15, 5, 0, tzinfo=UTC), monotonic=0.0)

    def test_quote_freshness(self):
        fresh = ContractQuote(
            yes_bid=0.54, yes_ask=0.55, no_bid=0.44, no_ask=0.46,
            quote_timestamp=datetime(2026, 8, 13, 15, 4, 55, tzinfo=UTC),
        )
        self.assertAlmostEqual(fresh.age_seconds(self.now), 5.0)
        self.assertTrue(fresh.is_fresh(self.now, 15.0))
        self.assertFalse(fresh.is_fresh(self.now, 2.0))

    def test_quote_without_timestamp_is_never_fresh(self):
        quote = ContractQuote(yes_bid=0.5, yes_ask=0.51, no_bid=0.48, no_ask=0.5)
        self.assertIsNone(quote.age_seconds(self.now))
        self.assertFalse(quote.is_fresh(self.now, 999999.0))

    def test_quote_sanity(self):
        self.assertTrue(ContractQuote(yes_bid=0.4, yes_ask=0.45).is_sane())
        self.assertFalse(ContractQuote(yes_bid=0.5, yes_ask=0.4).is_sane())      # crossed
        self.assertFalse(ContractQuote(yes_ask=1.4).is_sane())                   # out of range
        self.assertFalse(ContractQuote(yes_ask=-0.1).is_sane())
        self.assertFalse(ContractQuote(yes_ask=float("nan")).is_sane())

    def test_spread(self):
        quote = ContractQuote(yes_bid=0.54, yes_ask=0.57, no_bid=0.41, no_ask=0.46)
        self.assertAlmostEqual(quote.yes_spread, 0.03)
        self.assertAlmostEqual(quote.no_spread, 0.05)
        self.assertIsNone(ContractQuote(yes_ask=0.5).yes_spread)


class TestContractSpec(unittest.TestCase):
    def setUp(self):
        self.window = window_at(et(2026, 8, 13, 11, 0, 0))
        self.now = Instant(utc=datetime(2026, 8, 13, 15, 5, 0, tzinfo=UTC), monotonic=0.0)
        self.good_quote = ContractQuote(
            yes_bid=0.54, yes_ask=0.55, no_bid=0.44, no_ask=0.46,
            quote_timestamp=datetime(2026, 8, 13, 15, 4, 58, tzinfo=UTC),
        )

    def test_proxy_spec_never_unlocks_economics(self):
        """Audit E-1/E-2: a proxy reference must not enable EV, ever."""
        spec = ContractSpec(
            window=self.window,
            underlying="BTC-USD",
            reference_price=63400.0,
            reference_source=ReferenceSource.PROXY_WINDOW_OPEN,
            settlement_rule=SettlementRule.PROXY_TERMINAL_ABOVE_REFERENCE,
            quote=self.good_quote,     # even with a perfect, fresh quote
        )
        self.assertTrue(spec.has_reference)
        self.assertFalse(spec.reference_is_verified)
        self.assertFalse(spec.economics_available(self.now, 15.0))
        self.assertIn("PROXY / UNVERIFIED", spec.reference_display())

    def test_manual_spec_is_not_verified(self):
        spec = ContractSpec(
            window=self.window,
            underlying="BTC-USD",
            reference_price=63400.0,
            reference_source=ReferenceSource.MANUAL_LOCAL,
            settlement_rule=SettlementRule.TERMINAL_ABOVE_REFERENCE,
            quote=self.good_quote,
        )
        self.assertFalse(spec.reference_is_verified)
        self.assertFalse(spec.economics_available(self.now, 15.0))

    def test_official_verified_spec_unlocks_economics(self):
        spec = ContractSpec(
            window=self.window,
            underlying="BTC-USD",
            reference_price=63400.0,
            reference_source=ReferenceSource.OFFICIAL_PROVIDER,
            settlement_rule=SettlementRule.TERMINAL_ABOVE_REFERENCE,
            quote=self.good_quote,
        )
        self.assertTrue(spec.reference_is_verified)
        self.assertTrue(spec.economics_available(self.now, 15.0))

    def test_verified_reference_with_unknown_rule_is_not_verified(self):
        spec = ContractSpec(
            window=self.window,
            underlying="BTC-USD",
            reference_price=63400.0,
            reference_source=ReferenceSource.OFFICIAL_PROVIDER,
            settlement_rule=SettlementRule.UNKNOWN,
            quote=self.good_quote,
        )
        self.assertFalse(spec.reference_is_verified)
        self.assertFalse(spec.economics_available(self.now, 15.0))

    def test_stale_quote_disables_economics(self):
        stale = ContractQuote(
            yes_bid=0.54, yes_ask=0.55, no_bid=0.44, no_ask=0.46,
            quote_timestamp=datetime(2026, 8, 13, 15, 0, 0, tzinfo=UTC),   # 300s old
        )
        spec = ContractSpec(
            window=self.window,
            underlying="BTC-USD",
            reference_price=63400.0,
            reference_source=ReferenceSource.OFFICIAL_PROVIDER,
            settlement_rule=SettlementRule.TERMINAL_ABOVE_REFERENCE,
            quote=stale,
        )
        self.assertTrue(spec.reference_is_verified)
        self.assertFalse(spec.economics_available(self.now, 15.0))

    def test_missing_quote_disables_economics(self):
        spec = ContractSpec(
            window=self.window,
            underlying="BTC-USD",
            reference_price=63400.0,
            reference_source=ReferenceSource.OFFICIAL_PROVIDER,
            settlement_rule=SettlementRule.TERMINAL_ABOVE_REFERENCE,
        )
        self.assertFalse(spec.economics_available(self.now, 15.0))

    def test_key_is_per_asset(self):
        self.assertNotEqual(self.window.key_for("BTC-USD"), self.window.key_for("ETH-USD"))
        self.assertTrue(self.window.key_for("BTC-USD").endswith("|BTC-USD"))


class TestTimestampParsing(unittest.TestCase):
    """Audit D-8: a naive timestamp must be rejected, never assumed."""

    def test_aware_forms_parse(self):
        self.assertIsNotNone(parse_iso_utc("2026-08-13T15:00:00Z"))
        self.assertIsNotNone(parse_iso_utc("2026-08-13T11:00:00-04:00"))
        self.assertIsNotNone(parse_iso_utc("2026-08-13T15:00:00+00:00"))

    def test_naive_is_rejected(self):
        self.assertIsNone(parse_iso_utc("2026-08-13T15:00:00"))
        self.assertIsNone(parse_iso_utc("2026-08-13"))

    def test_garbage_is_rejected(self):
        self.assertIsNone(parse_iso_utc(""))
        self.assertIsNone(parse_iso_utc("not a timestamp"))
        self.assertIsNone(parse_iso_utc(None))  # type: ignore[arg-type]

    def test_z_and_offset_agree(self):
        a = parse_iso_utc("2026-08-13T15:00:00Z")
        b = parse_iso_utc("2026-08-13T11:00:00-04:00")
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
