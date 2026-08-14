import hashlib,json,tempfile,unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from mantis_v4.config import WebullCredentials
from mantis_v4.economics import *
from mantis_v4.entry import Decision,DecisionInputs,ProbabilityState,RiskDiagnostics

UTC=timezone.utc; NOW=datetime(2026,8,14,16,10,tzinfo=UTC)
def econ(**kw):
 d=dict(asset="BTC-USD",contract_id="c",reference=65000.,resolution_time=NOW+timedelta(minutes=5),settlement_rule="TERMINAL_ABOVE_REFERENCE",yes_bid=.58,yes_ask=.60,no_bid=.40,no_ask=.42,payout=1.,quote_timestamp=NOW-timedelta(seconds=2),quote_source="verified-manual",reference_verified=True,quote_verified=True)
 d.update(kw); return ContractEconomics(**d)
def inputs(p=.95,l=.90,sec=240): return DecisionInputs("BTC-USD","c",sec,ProbabilityState(p,l),RiskDiagnostics(2.5,40,.01,.1,crossings=2))

class FormulaTests(unittest.TestCase):
 def test_break_even(self): self.assertAlmostEqual(break_even_probability(.60),.60)
 def test_fee_adjusted_break_even(self): self.assertAlmostEqual(break_even_probability(.60,1,.02),.62)
 def test_gross_ev(self): self.assertAlmostEqual(expected_value(.95,.60),.35)
 def test_net_ev_verified_costs(self): self.assertAlmostEqual(expected_value(.95,.60,1,.03),.32)
 def test_max_price_edge_table_formula(self): self.assertAlmostEqual(maximum_purchase_price(.95,edge=.10),.85)
 def test_positive_and_negative(self):
  a=assess_economics(econ(),side="YES",model_probability=.95,lower_bound=.90,now=NOW,max_quote_age=15,expected_contract_id="c",expected_reference=65000)
  self.assertEqual(a.status,EconomicStatus.ROBUST_POSITIVE_EV); self.assertEqual(a.ev_label,"EV_BEFORE_UNVERIFIED_FEES")
  b=assess_economics(econ(yes_ask=.80,yes_bid=.78,no_bid=.20,no_ask=.22),side="YES",model_probability=.70,lower_bound=.65,now=NOW,max_quote_age=15,expected_contract_id="c",expected_reference=65000)
  self.assertEqual(b.status,EconomicStatus.NEGATIVE_EV)
 def test_point_positive_lcb_negative(self):
  a=assess_economics(econ(yes_ask=.94,yes_bid=.92,no_bid=.06,no_ask=.08),side="YES",model_probability=.95,lower_bound=.90,now=NOW,max_quote_age=15,expected_contract_id="c",expected_reference=65000)
  self.assertGreater(a.gross_ev,0); self.assertLess(a.lcb_gross_ev,0); self.assertEqual(a.status,EconomicStatus.NON_ROBUST_EV)
 def test_net_return_on_cost(self):
  e=econ(fee_per_contract=.02,fee_status=FeeStatus.VERIFIED,slippage_per_contract=.01,slippage_status=SlippageStatus.VERIFIED)
  a=assess_economics(e,side="YES",model_probability=.95,lower_bound=.90,now=NOW,max_quote_age=15,expected_contract_id="c",expected_reference=65000)
  self.assertAlmostEqual(a.net_ev,.32); self.assertAlmostEqual(a.expected_return_on_cost,.32/.63)

class ValidationTests(unittest.TestCase):
 def assess(self,e): return assess_economics(e,side="YES",model_probability=.95,lower_bound=.90,now=NOW,max_quote_age=15,expected_contract_id="c",expected_reference=65000)
 def test_stale(self): self.assertEqual(self.assess(econ(quote_timestamp=NOW-timedelta(seconds=16))).status,EconomicStatus.INVALID_STALE)
 def test_bad_bid_ask(self): self.assertEqual(self.assess(econ(yes_bid=.7,yes_ask=.6)).status,EconomicStatus.INVALID_STALE)
 def test_expired(self): self.assertEqual(self.assess(econ(resolution_time=NOW)).status,EconomicStatus.INVALID_STALE)
 def test_reference_and_contract_match(self): self.assertEqual(self.assess(econ(contract_id="wrong")).status,EconomicStatus.INVALID_STALE)
 def test_book_consistency(self): self.assertIn("CROSS_SIDE_BIDS_EXCEED_PAYOUT",self.assess(econ(yes_bid=.7,yes_ask=.72,no_bid=.5,no_ask=.52)).reasons)
 def test_fee_and_slippage_status(self):
  a=self.assess(econ()); self.assertIsNone(a.net_ev); self.assertEqual(a.fees_status,"UNKNOWN_FEES"); self.assertEqual(a.slippage_status,"SLIPPAGE_NOT_MODELED")

class ProviderTests(unittest.TestCase):
 def test_official_absent_credentials(self): self.assertEqual(WebullEconomicsProvider(WebullCredentials()).status,"AUTH_NOT_CONFIGURED")
 def test_proxy_never_unlocks(self): self.assertIsNone(ProxyEconomicsProvider().get_economics("BTC-USD","c",NOW))
 def test_verified_manual_and_fallback(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)/"economics.json"; r={"asset":"BTC-USD","contract_id":"c","reference":65000,"resolution_time":(NOW+timedelta(minutes=5)).isoformat(),"yes_ask":.6,"yes_bid":.58,"no_ask":.42,"no_bid":.4,"payout":1,"quote_timestamp":NOW.isoformat(),"settlement_rule":"TERMINAL_ABOVE_REFERENCE","source":"manual","verified":True}
   p.write_text(json.dumps({"contracts":[r]})); chain=EconomicsProviderChain([ProxyEconomicsProvider(),ManualEconomicsProvider(p),WebullEconomicsProvider(WebullCredentials())])
   self.assertIsNotNone(chain.get_economics("BTC-USD","c",NOW)); self.assertEqual(chain.last_provider,"verified-manual")
 def test_unverified_manual_rejected(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)/"e.json"; p.write_text(json.dumps([{"asset":"BTC-USD","contract_id":"c","verified":False}]))
   self.assertIsNone(ManualEconomicsProvider(p).get_economics("BTC-USD","c",NOW))

class DecisionTests(unittest.TestCase):
 def policy(self,**kw):
  base=Phase7Config().policy(); return EconomicDecisionPolicy(base.classification,base.maximum_quote_age,kw.get("edge",.05),kw.get("lcb",.02),kw.get("roc",0))
 def test_missing_economics_waits(self): self.assertEqual(decide_with_economics(inputs(),self.policy(),economics=None,now=NOW,reference=65000).reason_code,"CONTRACT_QUOTE_UNAVAILABLE")
 def test_negative_ev_no_trade(self): self.assertEqual(decide_with_economics(inputs(.95,.9),self.policy(),economics=econ(yes_ask=.98,yes_bid=.96,no_bid=.02,no_ask=.04),now=NOW,reference=65000).decision,Decision.NO_TRADE)
 def test_nonrobust_waits(self): self.assertEqual(decide_with_economics(inputs(),self.policy(),economics=econ(yes_ask=.94,yes_bid=.92,no_bid=.06,no_ask=.08),now=NOW,reference=65000).reason_code,"NON_ROBUST_EV")
 def test_edge_gate_and_enter(self):
  self.assertEqual(decide_with_economics(inputs(),self.policy(edge=.40,lcb=.40),economics=econ(),now=NOW,reference=65000).reason_code,"EDGE_TOO_SMALL")
  self.assertEqual(decide_with_economics(inputs(),self.policy(),economics=econ(),now=NOW,reference=65000).decision,Decision.ENTER_YES)
 def test_phase6_precondition_and_boundary(self):
  self.assertEqual(decide_with_economics(inputs(p=.8,l=.75),self.policy(),economics=econ(),now=NOW,reference=65000).reason_code,"PROBABILITY_TOO_LOW")
  self.assertEqual(decide_with_economics(inputs(sec=301),self.policy(),economics=econ(),now=NOW,reference=65000).reason_code,"EARLY_INSUFFICIENT_INFORMATION")
  self.assertEqual(decide_with_economics(inputs(sec=300),self.policy(),economics=econ(),now=NOW,reference=65000).decision,Decision.ENTER_YES)
 def test_v3_unchanged(self):
  path=Path(__file__).resolve().parents[1]/"mantis_15m_resolution_v3.py"
  self.assertEqual(hashlib.sha1(path.read_bytes()).hexdigest(),"cb7c65e3b00be5446c3bc403d7c043e39dc95eda")

if __name__=="__main__": unittest.main()
