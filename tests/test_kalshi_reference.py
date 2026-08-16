from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from mantis_v4.economics.fees import WEBULL_EVENT_OPENING_FEE
from mantis_v4.economics.kalshi_reference import (
    CURRENT_VALUE_PROVENANCE, ENDPOINT_MODEL, GATE_STATUS, MODEL_POLICY,
    QUALIFICATION_POLICY, REFERENCE_SOURCE, SETTLEMENT_PROVENANCE,
    HistoricalKalshiContract, KalshiFeeModel, chronological_three_way_split,
    build_kalshi_states, fetch_settled_contracts, kalshi_reference_probability,
    parse_historical_market,
)
import pandas as pd

UTC = timezone.utc
START = datetime(2026, 8, 15, 20, 30, tzinfo=UTC)


def market(**overrides):
    row = {
        "status": "finalized", "result": "yes", "strike_type": "greater_or_equal",
        "open_time": START.isoformat(), "close_time": (START + timedelta(minutes=15)).isoformat(),
        "floor_strike": "100.25", "expiration_value": "100.26",
        "ticker": "KXBTC15M-X", "event_ticker": "KXBTC15M-E",
    }
    row.update(overrides)
    return row


class KalshiReferencePolicyTests(unittest.TestCase):
    def test_actual_target_and_provenance_are_explicit(self):
        result = kalshi_reference_probability(current_value=Decimal("101"), target=Decimal("100"),
                                              sigma_remaining=.01,
                                              old_proxy_reference=Decimal("99"))
        self.assertEqual((result.model_policy, result.qualification_policy, result.gate_status),
                         (MODEL_POLICY, QUALIFICATION_POLICY, GATE_STATUS))
        self.assertEqual(result.reference_source, REFERENCE_SOURCE)
        self.assertEqual(result.settlement_reference_provenance, SETTLEMENT_PROVENANCE)
        self.assertEqual(result.current_value_provenance, CURRENT_VALUE_PROVENANCE)
        self.assertEqual(result.target, Decimal("100"))
        self.assertEqual(result.reference_gap_absolute, Decimal("1"))
        self.assertAlmostEqual(float(result.reference_gap_bps), 101.010101, places=5)

    def test_event_is_greater_or_equal_and_endpoint_model_is_not_sqrt60(self):
        result = kalshi_reference_probability(current_value=Decimal("100"), target=Decimal("100"),
                                              sigma_remaining=.01)
        self.assertIn("GTE", result.event_definition)
        self.assertEqual(result.endpoint_model, ENDPOINT_MODEL)
        self.assertIn("NO_SQRT60", result.endpoint_model)
        self.assertAlmostEqual(result.p_yes, .5)

    def test_old_proxy_is_diagnostic_not_probability_reference(self):
        a = kalshi_reference_probability(current_value=Decimal("101"), target=Decimal("100"),
                                         sigma_remaining=.01, old_proxy_reference=Decimal("80"))
        b = kalshi_reference_probability(current_value=Decimal("101"), target=Decimal("100"),
                                         sigma_remaining=.01, old_proxy_reference=Decimal("120"))
        self.assertEqual(a.p_yes, b.p_yes)
        self.assertNotEqual(a.reference_gap_bps, b.reference_gap_bps)

    def test_transferred_gates_are_separately_named(self):
        from mantis_v4.economics.kalshi_reference import TRANSFERRED_POLICY
        self.assertEqual((TRANSFERRED_POLICY.name, TRANSFERRED_POLICY.probability_threshold,
                          TRANSFERRED_POLICY.lcb_threshold, TRANSFERRED_POLICY.max_fragility,
                          TRANSFERRED_POLICY.max_disagreement, TRANSFERRED_POLICY.max_seconds_remaining,
                          TRANSFERRED_POLICY.max_crossing_probability, TRANSFERRED_POLICY.max_crossings),
                         ("KALSHI_H_V1", .95, .90, 50, .05, 300, .35, 4))
        self.assertNotEqual(TRANSFERRED_POLICY.name, "H_p0.95_l0.90_f50_d.05_t300")


class KalshiFeeTests(unittest.TestCase):
    def test_official_quadratic_taker_formula_and_rounding(self):
        cases = (("0.01", "0.01"), ("0.50", "0.02"), ("0.99", "0.01"),
                 ("0.1234", "0.01"))
        for price, expected in cases:
            with self.subTest(price=price):
                q = KalshiFeeModel.taker_fee(contracts=Decimal("1"), price=Decimal(price),
                                             fee_type="quadratic", fee_multiplier=Decimal("1"))
                self.assertEqual(q.fee, Decimal(expected))
                self.assertNotEqual(q.provenance, "WEBULL_OFFICIAL_FEE_SCHEDULE")
        self.assertEqual(WEBULL_EVENT_OPENING_FEE, Decimal("0.02"))

    def test_multiplier_and_whole_transaction_rounding(self):
        q = KalshiFeeModel.taker_fee(contracts=Decimal("10"), price=Decimal("0.50"),
                                    fee_type="quadratic", fee_multiplier=Decimal("2"))
        self.assertEqual(q.fee, Decimal("0.35"))

    def test_unknown_fee_type_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "FEE_UNVERIFIED"):
            KalshiFeeModel.taker_fee(contracts=Decimal("1"), price=Decimal(".5"),
                                    fee_type="flat", fee_multiplier=Decimal("1"))


class KalshiHistoricalTests(unittest.TestCase):
    def test_parse_finalized_market_and_gte_consistency(self):
        c = parse_historical_market("BTC-USD", "KXBTC15M", market())
        self.assertEqual((c.target, c.ending_benchmark, c.outcome_yes),
                         (Decimal("100.25"), Decimal("100.26"), 1))
        with self.assertRaisesRegex(ValueError, "conflicts"):
            parse_historical_market("BTC-USD", "KXBTC15M",
                                    market(result="no", expiration_value="100.26"))

    def test_wrong_window_and_unsettled_rejected(self):
        with self.assertRaisesRegex(ValueError, "quarter-hour"):
            parse_historical_market("BTC-USD", "KXBTC15M",
                                    market(close_time=(START + timedelta(minutes=14)).isoformat()))
        with self.assertRaisesRegex(ValueError, "not finalized"):
            parse_historical_market("BTC-USD", "KXBTC15M", market(status="active"))

    def test_window_grouped_chronological_holdout(self):
        rows = []
        for i in range(10):
            for asset in ("BTC-USD", "ETH-USD"):
                start = START + timedelta(minutes=15 * i)
                rows.append(HistoricalKalshiContract(asset, "S", f"M-{asset}-{i}", "E",
                    start, start + timedelta(minutes=15), Decimal("100"), Decimal("101"), 1, "yes"))
        dev, val, hold = chronological_three_way_split(rows)
        self.assertEqual((len(dev), len(val), len(hold)), (12, 4, 4))
        self.assertLess(max(x.window_end_utc for x in dev), min(x.window_end_utc for x in val))
        self.assertLess(max(x.window_end_utc for x in val), min(x.window_end_utc for x in hold))

    def test_public_history_reconstruction_is_bounded_and_deduplicated(self):
        class Client:
            def __init__(self): self.calls = []
            def get(self, path, **params):
                self.calls.append((path, params))
                return {"markets": [market()], "cursor": "again"}
        client = Client()
        rows = fetch_settled_contracts(client, asset="BTC-USD", series_ticker="KXBTC15M",
                                       max_pages_per_tier=2)
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(client.calls), 4)
        self.assertEqual({x[0] for x in client.calls}, {"/markets", "/historical/markets"})

    def test_state_builder_uses_kalshi_target_and_actual_outcome(self):
        frame = pd.DataFrame({
            "contract_id": ["c"], "asset": ["BTC-USD"], "group_key": ["w"],
            "window_epoch": [1], "seconds_remaining": [240.0], "spot": [101.0],
            "kalshi_target": [100.0], "realized_vol_1m": [.001],
            "sigma_remaining_return": [.002], "rv_5m": [.001], "rv_30m": [.001],
            "crossings": [0], "outcome_yes": [1],
        })
        state = build_kalshi_states(frame).iloc[0]
        self.assertGreater(state.p_yes, .99)
        self.assertEqual(state.outcome_yes, 1)


if __name__ == "__main__":
    unittest.main()
