from __future__ import annotations

import unittest
from decimal import Decimal
from pathlib import Path

from mantis_v4.manual_signal.core import (
    MAX_TOTAL_ENTRY_COST, V2_POLICY, V21_POLICY, V22_MIN_CONSERVATIVE_EDGE,
    V22_POLICY, evaluate_candidate, quality_gate_failures, select_primary_signal,
)
from tests.test_kalshi_manual_signal import ASSETS, NOW, fees, mapping, quote, snapshot


class ManualV22ActivePolicyTests(unittest.TestCase):
    def failures(self, **changes):
        snap=snapshot(p=changes.pop("p",.88),lcb=changes.pop("lcb",.83))
        for name,value in changes.items(): setattr(snap,name,value)
        return {row["gate"] for row in quality_gate_failures(snap,V22_POLICY)}

    def evaluate(self, *, price=100.03, p=.90, lcb=.85, ask=Decimal("0.75"), seconds=890):
        return evaluate_candidate(snapshot=snapshot(price=price,p=p,lcb=lcb,seconds=seconds),
            mapping=mapping(),quote=quote(yes=ask),quote_status="READY",
            dev_p95_bps=Decimal("5.47"),dev_p50_bps=Decimal("1.25"),
            fee_metadata=fees(),now=NOW,policy=V22_POLICY)

    def test_exact_version_and_thresholds_without_mutating_prior_policies(self):
        self.assertEqual(V22_POLICY.version,"EXPERIMENTAL_MANUAL_SIGNAL_V2_2_ACTIVE")
        self.assertEqual((V22_POLICY.confidence,V22_POLICY.conservative_probability,
            V22_POLICY.fragility,V22_POLICY.disagreement,V22_POLICY.crossing_probability,
            V22_POLICY.crossings,V22_POLICY.min_conservative_edge),
            (.88,.83,60,.075,.50,6,Decimal("0.01")))
        self.assertEqual((V2_POLICY.confidence,V2_POLICY.conservative_probability,V2_POLICY.min_conservative_edge),
                         (.95,.90,Decimal("0.02")))
        self.assertEqual((V21_POLICY.confidence,V21_POLICY.conservative_probability,V21_POLICY.min_conservative_edge),
                         (.92,.87,Decimal("0.015")))

    def test_quality_boundaries(self):
        self.assertIn("CONFIDENCE",self.failures(p=.879)); self.assertNotIn("CONFIDENCE",self.failures(p=.88))
        self.assertIn("CONSERVATIVE",self.failures(lcb=.829)); self.assertNotIn("CONSERVATIVE",self.failures(lcb=.83))
        self.assertNotIn("FRAGILITY",self.failures(fragility=60)); self.assertIn("FRAGILITY",self.failures(fragility=60.01))
        self.assertNotIn("DISAGREEMENT",self.failures(disagreement=.075)); self.assertIn("DISAGREEMENT",self.failures(disagreement=.0751))
        self.assertNotIn("CROSSING PROBABILITY",self.failures(crossing_probability=.50)); self.assertIn("CROSSING PROBABILITY",self.failures(crossing_probability=.501))
        self.assertNotIn("CROSSINGS",self.failures(reference_crossings=6)); self.assertIn("CROSSINGS",self.failures(reference_crossings=7))

    def test_reference_tiers_use_development_p95_and_p50(self):
        robust=self.evaluate(price=100.06)
        caution=self.evaluate(price=100.03)
        ambiguous=self.evaluate(price=100.01)
        self.assertEqual(robust["reference_risk"],"REFERENCE_ROBUST")
        self.assertEqual(caution["reference_risk"],"REFERENCE_CAUTION")
        self.assertEqual(caution["status"],"ELIGIBLE")
        self.assertEqual(caution["reference_warning"],"REFERENCE CAUTION — PROXY/SETTLEMENT BASIS RISK")
        self.assertEqual((ambiguous["reference_risk"],ambiguous["status"]),("REFERENCE_AMBIGUOUS","REF AMBIGUOUS"))
        self.assertEqual(caution["reference_caution_bound_bps"],"1.25")

    def test_early_signal_economics_cost_cap_and_no_forcing(self):
        early=self.evaluate(seconds=899)
        self.assertEqual(early["status"],"ELIGIBLE")
        self.assertGreaterEqual(Decimal(early["conservative_edge"]),V22_MIN_CONSERVATIVE_EDGE)
        expensive=self.evaluate(p=.99,lcb=.97,ask=Decimal("0.94"))
        self.assertEqual(expensive["status"],"EXPENSIVE CONTRACT")
        self.assertEqual(MAX_TOTAL_ENTRY_COST,Decimal("0.95"))
        failed=[{**self.evaluate(),"asset":a,"status":"CONF FAIL","economically_valid":False} for a in ASSETS]
        self.assertIsNone(select_primary_signal(failed,policy=V22_POLICY)["selected"])

    def test_live_operator_and_parallel_telemetry_are_v22(self):
        live=Path("mantis_v4/manual_signal/live.py").read_text(encoding="utf-8")
        entry=Path("mantis_v4_live.py").read_text(encoding="utf-8")
        self.assertIn("policy=V22_POLICY",live)
        self.assertIn("(V2_POLICY,V21_POLICY,V22_POLICY)",live)
        self.assertIn("initial_manual_selection(V22_POLICY)",entry)
        self.assertNotIn("place_order",live); self.assertNotIn("submit_order",live)


if __name__=="__main__": unittest.main()
