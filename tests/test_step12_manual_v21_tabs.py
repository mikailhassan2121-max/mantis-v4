from __future__ import annotations

import unittest
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from mantis_v4.manual_signal.core import (
    ASSETS, MAX_TOTAL_ENTRY_COST, POLICY_VERSION, V21_MIN_CONSERVATIVE_EDGE,
    V21_POLICY, apply_signal_lock, evaluate_candidate, initial_manual_selection,
    quality_gate_failures, select_primary_signal,
)
from tests.test_kalshi_manual_signal import NOW, candidate, fees, mapping, quote, snapshot

ROOT=Path(__file__).resolve().parents[1]
WEB=ROOT/"mantis_v4"/"ui"/"web"


class ManualV21PolicyTests(unittest.TestCase):
    def failures(self, **changes):
        values=dict(p=.92,lcb=.87)
        values.update({k:v for k,v in changes.items() if k in {"p","lcb"}})
        snap=snapshot(p=values["p"],lcb=values["lcb"])
        for name,value in changes.items():
            if name not in values:setattr(snap,name,value)
        return {row["gate"] for row in quality_gate_failures(snap,V21_POLICY)}

    def test_versions_and_v2_remain_distinct(self):
        self.assertEqual(POLICY_VERSION,"EXPERIMENTAL_MANUAL_SIGNAL_V2")
        self.assertEqual(V21_POLICY.version,"EXPERIMENTAL_MANUAL_SIGNAL_V2_1")
        self.assertEqual(initial_manual_selection()["selection_policy"],POLICY_VERSION)
        self.assertEqual(initial_manual_selection(V21_POLICY)["selection_policy"],V21_POLICY.version)

    def test_exact_quality_boundaries(self):
        self.assertIn("CONFIDENCE",self.failures(p=.919)); self.assertNotIn("CONFIDENCE",self.failures(p=.920))
        self.assertIn("CONSERVATIVE",self.failures(lcb=.869)); self.assertNotIn("CONSERVATIVE",self.failures(lcb=.870))
        self.assertNotIn("FRAGILITY",self.failures(fragility=55)); self.assertIn("FRAGILITY",self.failures(fragility=55.01))
        self.assertNotIn("DISAGREEMENT",self.failures(disagreement=.06)); self.assertIn("DISAGREEMENT",self.failures(disagreement=.0601))
        self.assertNotIn("CROSSING PROBABILITY",self.failures(crossing_probability=.42)); self.assertIn("CROSSING PROBABILITY",self.failures(crossing_probability=.4201))
        self.assertNotIn("CROSSINGS",self.failures(reference_crossings=5)); self.assertIn("CROSSINGS",self.failures(reference_crossings=6))

    def test_v21_economics_reference_and_early_timing(self):
        row=evaluate_candidate(snapshot=snapshot(seconds=850,p=.95,lcb=.875),mapping=mapping(),
            quote=quote(yes=Decimal("0.85")),quote_status="READY",dev_p95_bps=Decimal("5.47"),
            fee_metadata=fees(),now=NOW,policy=V21_POLICY)
        self.assertEqual(row["status"],"ELIGIBLE")
        self.assertEqual(Decimal(row["conservative_edge"]),V21_MIN_CONSERVATIVE_EDGE)
        below=evaluate_candidate(snapshot=snapshot(seconds=850,p=.95,lcb=.874),mapping=mapping(),
            quote=quote(yes=Decimal("0.85")),quote_status="READY",dev_p95_bps=Decimal("5.47"),
            fee_metadata=fees(),now=NOW,policy=V21_POLICY)
        self.assertEqual(below["status"],"ECON FAIL")
        ambiguous=evaluate_candidate(snapshot=snapshot(price=100.01,p=.95,lcb=.90),mapping=mapping(),quote=quote(),
            quote_status="READY",dev_p95_bps=Decimal("5.47"),fee_metadata=fees(),now=NOW,policy=V21_POLICY)
        self.assertEqual(ambiguous["status"],"REF AMBIGUOUS")

    def test_expensive_negative_and_no_forced_signal(self):
        expensive=evaluate_candidate(snapshot=snapshot(p=.999,lcb=.99),mapping=mapping(),
            quote=quote(yes=Decimal("0.98")),quote_status="READY",dev_p95_bps=Decimal("5.47"),
            fee_metadata=fees(),now=NOW,policy=V21_POLICY)
        self.assertEqual(expensive["status"],"EXPENSIVE CONTRACT")
        self.assertEqual(MAX_TOTAL_ENTRY_COST,Decimal("0.95"))
        rows=[{**candidate(asset),"status":"CONF FAIL","economically_valid":False} for asset in ASSETS]
        self.assertIsNone(select_primary_signal(rows,policy=V21_POLICY)["selected"])

    def test_all_four_rank_then_lock_and_rollover(self):
        rows=[candidate(asset) for asset in reversed(ASSETS)]
        rows[2]["conservative_edge"]="0.30"
        selection=select_primary_signal(rows,policy=V21_POLICY)
        self.assertEqual(len(selection["candidates"]),4)
        locked,lock=apply_signal_lock(selection,None,NOW,policy=V21_POLICY)
        replacement=select_primary_signal([{**candidate(a,"NO"),"conservative_edge":"0.50"} for a in ASSETS],policy=V21_POLICY)
        later,still=apply_signal_lock(replacement,lock,NOW+timedelta(seconds=30),policy=V21_POLICY)
        self.assertEqual((still["asset"],still["side"]),(lock["asset"],lock["side"]))
        fresh,_=apply_signal_lock(replacement,None,NOW+timedelta(minutes=15),policy=V21_POLICY)
        self.assertEqual(fresh["selected"]["side"],"NO")


class AuxiliaryTabRegressionTests(unittest.TestCase):
    def test_tabs_are_bound_once_and_switch_all_four_views(self):
        app=(WEB/"app.js").read_text(encoding="utf-8")
        html=(WEB/"index.html").read_text(encoding="utf-8")
        for view in ("contract","surveillance","forward","diagnostics"):
            self.assertIn(f'data-view="{view}"',html); self.assertIn(f'id="view_{view}"',html)
        self.assertIn('tab.dataset.mantisBound === "true"',app)
        self.assertIn('v.classList.toggle("active", v.id === "view_" + target)',app)
        self.assertEqual(app.count("new EventSource("),1)

    def test_manual_auxiliary_views_use_authoritative_state(self):
        modules=(WEB/"modules.js").read_text(encoding="utf-8")
        for token in ("operator.candidate_rankings","forward_shadow_summary","signal_frequency",
                      "BLOCKING GATES","FORWARD MILESTONES","ACTIVE SSE CLIENTS","LAST ERROR"):
            self.assertIn(token,modules)
        app=(WEB/"app.js").read_text(encoding="utf-8")
        self.assertIn('else if (view === "surveillance") M.renderSurveillance(snapshot)',app)
        self.assertIn('else if (view === "forward") M.renderForwardView(snapshot)',app)
        self.assertIn('else if (view === "diagnostics") M.renderDiagnostics(snapshot)',app)
        self.assertIn("if (manualCenter) M.renderManualSignalCenter",app)


if __name__=="__main__": unittest.main()
