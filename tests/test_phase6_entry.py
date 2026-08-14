"""Phase 6 decision, replay, validation and wording invariants."""
import unittest
import numpy as np
import pandas as pd

from mantis_v4.entry import (Decision, DecisionInputs, DecisionPolicy, ProbabilityState,
    RiskDiagnostics, apply_policy_once, decide, entry_time_bucket, evaluate_entries,
    select_policy_on_development)


def inputs(**changes):
    d=dict(asset="BTC",contract_id="c1",seconds_remaining=180,
           probability=ProbabilityState(.92,.87),
           risk=RiskDiagnostics(2.0,30,.02,.10,crossings=1))
    d.update(changes); return DecisionInputs(**d)


def frame(n_contracts=40):
    rows=[]
    for c in range(n_contracts):
        y=c%2
        for sec,p in [(600,.65),(300,.82),(120,.94)]:
            py=p if y else 1-p
            rows.append(dict(contract_id=f"c{c}",asset="BTC",group_key=f"w{c//2}",
                window_epoch=1000+c//2*900,seconds_remaining=sec,p_yes=py,
                lower_bound=max(.5,p-.04),normal_z=2 if y else -2,fragility=25,
                disagreement=.01,crossing_risk=.08,crossings=1,outcome_yes=y))
    return pd.DataFrame(rows)


class DecisionTests(unittest.TestCase):
    def test_probability_and_lcb_thresholds(self):
        self.assertEqual(decide(inputs(),DecisionPolicy("p",.95)).reason_code,"PROBABILITY_TOO_LOW")
        self.assertEqual(decide(inputs(),DecisionPolicy("l",.8,lcb_threshold=.9)).reason_code,"CONSERVATIVE_BOUND_TOO_LOW")

    def test_fragility_and_disagreement_gates(self):
        self.assertEqual(decide(inputs(),DecisionPolicy("f",max_fragility=20)).reason_code,"FRAGILITY_TOO_HIGH")
        self.assertEqual(decide(inputs(),DecisionPolicy("d",max_disagreement=.01)).reason_code,"DISAGREEMENT_TOO_HIGH")

    def test_wait_enter_no_and_no_trade(self):
        self.assertEqual(decide(inputs(),DecisionPolicy("ok",.9)).decision,Decision.ENTER_YES)
        no=inputs(probability=ProbabilityState(.06,.90),risk=RiskDiagnostics(-2,20,.01,.05))
        self.assertEqual(decide(no,DecisionPolicy("ok",.9)).decision,Decision.ENTER_NO)
        self.assertEqual(decide(inputs(seconds_remaining=0),DecisionPolicy("late",no_trade_seconds=1)).decision,Decision.NO_TRADE)

    def test_data_hold_and_exact_boundary(self):
        self.assertEqual(decide(inputs(data_fresh=False),DecisionPolicy("x")).decision,Decision.DATA_HOLD)
        self.assertEqual(decide(inputs(seconds_remaining=900),DecisionPolicy("x",max_seconds_remaining=900)).decision,Decision.ENTER_YES)
        self.assertEqual(decide(inputs(seconds_remaining=901),DecisionPolicy("x")).decision,Decision.DATA_HOLD)

    def test_one_entry_max_and_hold_to_resolution(self):
        out=apply_policy_once(frame(4),DecisionPolicy("x",.8))
        self.assertEqual(len(out),4); self.assertTrue(out.entered.all())
        self.assertTrue((out.seconds_remaining==300).all())

    def test_no_trade_contract(self):
        out=apply_policy_once(frame(2).assign(p_yes=.51,lower_bound=.50),DecisionPolicy("x",.9))
        self.assertFalse(out.entered.any())

    def test_grouped_metrics_and_coverage(self):
        out=apply_policy_once(frame(10),DecisionPolicy("x",.9))
        m=evaluate_entries(out)
        self.assertEqual(m["contracts_traded"],10); self.assertEqual(m["coverage"],1)
        self.assertEqual(m["accuracy"],1); self.assertLessEqual(m["n_windows"],10)

    def test_entry_buckets(self):
        self.assertEqual(entry_time_bucket(600),"T-600 to T-450")
        self.assertEqual(entry_time_bucket(450),"T-450 to T-300")
        self.assertEqual(entry_time_bucket(30),"T-30 to resolution")

    def test_deterministic_selection_and_no_holdout_argument(self):
        candidates=[DecisionPolicy("a",.8),DecisionPolicy("b",.9)]
        a,_=select_policy_on_development(frame(),candidates)
        b,_=select_policy_on_development(frame(),candidates)
        self.assertEqual(a,b)
        self.assertNotIn("holdout",select_policy_on_development.__code__.co_varnames[:select_policy_on_development.__code__.co_argcount])

    def test_no_future_state_changes_earlier_decision(self):
        f=frame(2); policy=DecisionPolicy("x",.8)
        before=apply_policy_once(f,policy)
        f.loc[f.seconds_remaining==120,"p_yes"]=1-f.loc[f.seconds_remaining==120,"p_yes"]
        after=apply_policy_once(f,policy)
        pd.testing.assert_series_equal(before.decision,after.decision)
        pd.testing.assert_series_equal(before.seconds_remaining,after.seconds_remaining)

    def test_no_economic_claim(self):
        r=decide(inputs(),DecisionPolicy("x",.9))
        self.assertEqual(r.ev_status,"UNAVAILABLE")
        forbidden=("profit", "roi", "expected value")
        self.assertFalse(any(x in " ".join(r.reasons).lower() for x in forbidden))

if __name__ == "__main__": unittest.main()
