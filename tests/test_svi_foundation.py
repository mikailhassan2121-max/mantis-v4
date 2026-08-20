from __future__ import annotations

from datetime import datetime, timezone
import tempfile
from pathlib import Path
import unittest

from mantis_v4.manual_signal.core import V2_POLICY, V21_POLICY, V22_POLICY
from saaf_ventures_intelligence.agents.mantis_adapter import MantisAdapter
from saaf_ventures_intelligence.agents.market_implied import KalshiMarketImpliedBenchmark
from saaf_ventures_intelligence.agents.reference_distance import ReferenceDistanceShadow
from saaf_ventures_intelligence.agents.base import SpecialistAgent
from saaf_ventures_intelligence.contracts import AgentContext, ExecutionMode, Side, SignalCandidate
from saaf_ventures_intelligence.registry import AgentRegistry
from saaf_ventures_intelligence.events import JsonlEventSink
from saaf_ventures_intelligence.events import AuditEvent, read_events
from saaf_ventures_intelligence.governance import SpecialistAdmissionPolicy
from saaf_ventures_intelligence.probabilities import CalibrationObservation, evaluate_calibration
from saaf_ventures_intelligence.outcomes import join_verified_forecasts, resolved_evidence_report
from saaf_ventures_intelligence.operations import audit_evidence, evidence_manifest, operational_report
from saaf_ventures_intelligence.replay import EvidenceReplay
from saaf_ventures_intelligence.risk import RiskEngine
from saaf_ventures_intelligence.supervisors import MarketSupervisor
from saaf_ventures_intelligence.ui import command_center_payload
from saaf_ventures_intelligence.ui import publish_to_mantis

UTC = timezone.utc
NOW = datetime(2026, 8, 19, 12, tzinfo=UTC)


def context(candidate):
    return AgentContext(NOW, "KALSHI", "CRYPTO_EVENT_15M", {
        "selection": {"candidates": [candidate]}
    })


class StaticAgent(SpecialistAgent):
    signal_family="TEST_PROBABILITY"; supported_markets=("KALSHI",); supported_instruments=("BTC-USD",)
    def __init__(self,name,group,candidate):
        self.name=name; self.version="1"; self.correlation_group=group; self.candidate=candidate
    def analyze(self,context): return (self.candidate,)


class ShadowAgent(StaticAgent):
    role = "SHADOW"


def static_candidate(agent,side,probability):
    return SignalCandidate(agent,"1","TEST_POLICY","KALSHI","BTC-USD",side,probability,
        probability,.50,300,NOW,"test",True,"READY",{"contract_id":"BTC|window|15m"})


class SviFoundationTests(unittest.TestCase):
    def test_mantis_adapter_preserves_active_and_historical_policies(self):
        before = (V2_POLICY, V21_POLICY, V22_POLICY)
        row = {"asset": "BTC", "side": "YES", "confidence": .9,
               "conservative_probability": .85, "ask": "0.75",
               "seconds_remaining": 420, "economically_valid": True,
               "status": "ELIGIBLE", "policy_version": V22_POLICY.version}
        candidate = MantisAdapter().analyze(context(row))[0]
        self.assertEqual(candidate.side, Side.YES)
        self.assertEqual(candidate.policy_version, V22_POLICY.version)
        self.assertEqual(candidate.attributes["status"], "ELIGIBLE")
        self.assertEqual(before, (V2_POLICY, V21_POLICY, V22_POLICY))

    def test_market_implied_agent_is_verified_quote_benchmark_only(self):
        row={"asset":"BTC-USD","side":"YES","confidence":.8,"conservative_probability":.7,
             "contract_id":"BTC|window|15m","seconds_remaining":300,"quote_verified":True,
             "yes_bid":".58","yes_ask":".62","no_bid":".38","no_ask":".42"}
        candidate=KalshiMarketImpliedBenchmark().analyze(context(row))[0]
        self.assertAlmostEqual(candidate.attributes["probability_yes"],.6)
        self.assertEqual(candidate.side,Side.YES)
        self.assertTrue(candidate.attributes["benchmark_only"])
        self.assertFalse(candidate.attributes["actionable"])
        self.assertFalse(candidate.economically_valid)

    def test_market_implied_agent_fails_closed_without_verified_consistent_book(self):
        base={"asset":"BTC-USD","contract_id":"BTC|window|15m","seconds_remaining":300,
              "yes_bid":".58","yes_ask":".62","no_bid":".38","no_ask":".42"}
        self.assertFalse(KalshiMarketImpliedBenchmark().analyze(context(base)))
        inconsistent={**base,"quote_verified":True,"no_bid":".05","no_ask":".10"}
        self.assertFalse(KalshiMarketImpliedBenchmark().analyze(context(inconsistent)))

    def test_reference_distance_shadow_is_independent_fail_closed_and_non_actionable(self):
        row={"asset":"BTC-USD","contract_id":"BTC|window|15m","target":"100",
             "proxy_current":"100.10","seconds_remaining":300,
             "window_end_utc":"2026-08-19T12:15:00+00:00","confidence":.01,
             "yes_bid":.99,"yes_ask":1.0}
        candidate=ReferenceDistanceShadow().analyze(context(row))[0]
        self.assertEqual(candidate.side,Side.YES)
        self.assertGreater(candidate.probability,.5)
        self.assertFalse(candidate.economically_valid)
        self.assertFalse(candidate.attributes["uses_mantis_probability"])
        self.assertFalse(candidate.attributes["uses_kalshi_quote"])
        self.assertFalse(candidate.attributes["actionable"])
        self.assertFalse(ReferenceDistanceShadow().analyze(context({**row,"target":"bad"})))

    def test_reference_distance_shadow_is_excluded_but_gets_resolved_governance_evidence(self):
        row={"asset":"BTC-USD","side":"YES","confidence":.8,"conservative_probability":.7,
             "ask":.60,"economically_valid":True,"contract_id":"BTC|window|15m",
             "target":"100","proxy_current":"100.10","window_end_utc":"2026-08-19T12:15:00+00:00",
             "seconds_remaining":300,"quote_verified":True,"yes_bid":.58,"yes_ask":.62,
             "no_bid":.38,"no_ask":.42}
        with tempfile.TemporaryDirectory() as tmp:
            evidence=Path(tmp)/"events.jsonl"; resolutions=Path(tmp)/"resolutions.jsonl"
            result=MarketSupervisor((MantisAdapter(),KalshiMarketImpliedBenchmark(),
                ReferenceDistanceShadow()),events=JsonlEventSink(evidence)).evaluate(context(row))
            self.assertEqual([item.candidate.agent for item in result.opportunities],["MANTIS"])
            self.assertEqual(result.consensus[0].agents,("MANTIS",))
            self.assertEqual([item.agent for item in result.shadows],["REFERENCE_DISTANCE_SHADOW"])
            resolutions.write_text('{"contract_id":"BTC|window|15m","result":"YES","resolution_status":"VERIFIED","resolved_at_utc":"2026-08-19T12:15:00+00:00","settlement_source":"CF_BENCHMARKS"}\n',encoding="utf-8")
            report=resolved_evidence_report(evidence,resolutions,minimum_sample=1)
            comparison=next(item for item in report["benchmark_comparisons"]
                if item["agent"]=="REFERENCE_DISTANCE_SHADOW")
            self.assertEqual((comparison["role"],comparison["overlap"]),("SHADOW",1))
            self.assertIsNone(comparison["brier_improvement_lower_95"])
            self.assertEqual(report["asset_comparisons"][0]["instrument"],"BTC-USD")
            self.assertTrue(report["asset_comparisons"][0]["sample_qualified"])
            self.assertEqual(report["complementarity"][0]["overlap"],1)
            self.assertIn("BRIER_IMPROVEMENT_NOT_STATISTICALLY_ESTABLISHED",
                          report["admission_governance"][0]["reasons"])
            self.assertFalse(report["admission_governance"][0]["automatic_promotion"])
            self.assertFalse(report["automatic_promotion"])

    def test_benchmark_is_excluded_from_ranking_and_consensus_but_recorded(self):
        row={"asset":"BTC-USD","side":"YES","confidence":.8,"conservative_probability":.7,
             "ask":".60","economically_valid":True,"contract_id":"BTC|window|15m",
             "seconds_remaining":300,"quote_verified":True,"yes_bid":".58","yes_ask":".62",
             "no_bid":".38","no_ask":".42"}
        result=MarketSupervisor((MantisAdapter(),KalshiMarketImpliedBenchmark())).evaluate(context(row))
        self.assertEqual([x.candidate.agent for x in result.opportunities],["MANTIS"])
        self.assertEqual([x.agent for x in result.benchmarks],["KALSHI_MARKET_IMPLIED"])
        self.assertEqual(result.consensus[0].agents,("MANTIS",))
        payload=command_center_payload(result)
        self.assertFalse(payload["benchmarks"][0]["actionable"])
        self.assertEqual(payload["registry"]["specialist_count"],2)

    def test_resolved_report_compares_mantis_to_market_on_identical_contracts(self):
        row={"asset":"BTC-USD","side":"YES","confidence":.8,"conservative_probability":.7,
             "ask":".60","economically_valid":True,"contract_id":"BTC|window|15m",
             "seconds_remaining":300,"quote_verified":True,"yes_bid":".58","yes_ask":".62",
             "no_bid":".38","no_ask":".42"}
        with tempfile.TemporaryDirectory() as tmp:
            evidence=Path(tmp)/"events.jsonl"; resolutions=Path(tmp)/"resolutions.jsonl"
            MarketSupervisor((MantisAdapter(),KalshiMarketImpliedBenchmark()),
                events=JsonlEventSink(evidence)).evaluate(context(row))
            resolutions.write_text('{"contract_id":"BTC|window|15m","result":"YES","resolution_status":"VERIFIED","resolved_at_utc":"2026-08-19T12:15:00+00:00","settlement_source":"CF_BENCHMARKS"}\n',encoding="utf-8")
            comparison=resolved_evidence_report(evidence,resolutions,minimum_sample=1)["benchmark_comparisons"][0]
            self.assertEqual((comparison["agent"],comparison["benchmark_agent"],comparison["overlap"]),
                             ("MANTIS","KALSHI_MARKET_IMPLIED",1))
            self.assertGreater(comparison["brier_improvement"],0)

    def test_invalid_economics_is_blocked_never_forced(self):
        row = {"asset": "ETH", "side": "YES", "confidence": .99,
               "conservative_probability": .97, "ask": "0.60",
               "economically_valid": False, "status": "QUOTE UNAVAILABLE"}
        result = MarketSupervisor((MantisAdapter(),)).evaluate(context(row))
        self.assertEqual(result.execution_mode, ExecutionMode.MANUAL_ONLY)
        self.assertFalse(result.opportunities)
        self.assertEqual(result.blocked[0].risk.reasons, ("ECONOMICS_NOT_VALIDATED",))

    def test_supervisor_ranks_reviewable_candidates_and_ui_is_read_only(self):
        rows = [
            {"asset": "BTC", "side": "YES", "confidence": .90,
             "conservative_probability": .85, "ask": "0.75", "economically_valid": True},
            {"asset": "SOL", "side": "NO", "confidence": .92,
             "conservative_probability": .88, "ask": "0.70", "economically_valid": True},
        ]
        ctx = AgentContext(NOW, "KALSHI", "CRYPTO_EVENT_15M", {"selection": {"candidates": rows}})
        result = MarketSupervisor((MantisAdapter(),), risk=RiskEngine()).evaluate(ctx)
        self.assertEqual([x.candidate.instrument for x in result.opportunities], ["SOL", "BTC"])
        payload = command_center_payload(result)
        self.assertTrue(payload["observation_only"])
        self.assertEqual(payload["execution_mode"], "MANUAL_ONLY")
        self.assertNotIn("order", payload)
        self.assertTrue(all(row.status=="PASS_THROUGH" for row in result.consensus))
        self.assertEqual(payload["registry"]["specialist_count"],1)

    def test_registry_rejects_duplicate_specialist_identity(self):
        candidate=static_candidate("A",Side.YES,.8)
        with self.assertRaises(ValueError):
            AgentRegistry((StaticAgent("A","G1",candidate),StaticAgent("A","G2",candidate)))

    def test_shadow_specialist_is_recorded_but_never_ranked_or_in_consensus(self):
        advisory=StaticAgent("A","G1",static_candidate("A",Side.YES,.8))
        shadow=ShadowAgent("S","G2",static_candidate("S",Side.YES,.95))
        result=MarketSupervisor((advisory,shadow)).evaluate(AgentContext(NOW,"KALSHI","BTC-USD",{}))
        self.assertEqual([row.candidate.agent for row in result.opportunities],["A"])
        self.assertEqual(result.consensus[0].agents,("A",))
        self.assertEqual([row.agent for row in result.shadows],["S"])
        self.assertFalse(command_center_payload(result)["shadows"][0]["actionable"])

    def test_admission_policy_only_marks_shadow_as_eligible_for_human_review(self):
        policy=SpecialistAdmissionPolicy(minimum_verified=10,minimum_overlap=5)
        decision=policy.evaluate(agent="S",role="SHADOW",verified_samples=12,
            benchmark_overlap=8,brier_improvement=.02,log_loss_improvement=.01,
            complementarity=.2,brier_improvement_lower_bound=.005,
            log_loss_improvement_lower_bound=.002,recent_brier_improvement=.01,
            asset_coverage=3,assets_meeting_minimum=3,
            worst_asset_brier_lower_bound=.001)
        self.assertEqual(decision.status,"ELIGIBLE_FOR_HUMAN_REVIEW")
        self.assertFalse(decision.automatic_promotion)
        blocked=policy.evaluate(agent="S",role="SHADOW",verified_samples=2,
            benchmark_overlap=1,brier_improvement=None,log_loss_improvement=None,
            complementarity=None)
        self.assertEqual(blocked.status,"NOT_ELIGIBLE")
        self.assertIn("INSUFFICIENT_VERIFIED_OUTCOMES",blocked.reasons)

    def test_admission_v2_rejects_point_win_without_uncertainty_stability_and_coverage(self):
        policy=SpecialistAdmissionPolicy(minimum_verified=2,minimum_overlap=2)
        decision=policy.evaluate(agent="S",role="SHADOW",verified_samples=10,
            benchmark_overlap=10,brier_improvement=.05,log_loss_improvement=.04,
            complementarity=.2,brier_improvement_lower_bound=-.01,
            log_loss_improvement_lower_bound=None,recent_brier_improvement=-.02,
            asset_coverage=1,assets_meeting_minimum=1,
            worst_asset_brier_lower_bound=-.01)
        self.assertEqual(decision.status,"NOT_ELIGIBLE")
        self.assertIn("BRIER_IMPROVEMENT_NOT_STATISTICALLY_ESTABLISHED",decision.reasons)
        self.assertIn("RECENT_PERIOD_STABILITY_NOT_ESTABLISHED",decision.reasons)
        self.assertIn("INSUFFICIENT_ASSET_COVERAGE",decision.reasons)
        self.assertIn("CROSS_ASSET_ROBUSTNESS_NOT_ESTABLISHED",decision.reasons)

    def test_resolved_report_requires_repeated_cross_asset_robustness(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence=Path(tmp)/"events.jsonl"; resolutions=Path(tmp)/"resolutions.jsonl"
            candidates=[]; resolution_rows=[]
            for asset in ("BTC-USD","ETH-USD","SOL-USD"):
                for index in range(2):
                    contract=f"{asset}|window|{index}"
                    common={"contract_id":contract,"instrument":asset,"side":"YES"}
                    candidates.extend((
                        {**common,"agent":"MANTIS","policy_version":"M","probability":.75,
                         "role":"ADVISORY","rank":1,"risk_disposition":"ALLOW_REVIEW"},
                        {**common,"agent":"KALSHI_MARKET_IMPLIED","policy_version":"B","probability":.60,
                         "role":"BENCHMARK","rank":None,"risk_disposition":"BENCHMARK_ONLY"},
                        {**common,"agent":"S","policy_version":"S1","probability":.95,
                         "role":"SHADOW","rank":None,"risk_disposition":"SHADOW_ONLY"}))
                    resolution_rows.append('{"contract_id":"'+contract+'","result":"YES",'
                        '"resolution_status":"VERIFIED","resolved_at_utc":"2026-08-19T12:15:00+00:00",'
                        '"settlement_source":"CF_BENCHMARKS"}')
            JsonlEventSink(evidence).append(AuditEvent("multi","SUPERVISOR_EVALUATION",NOW,"run",
                {"execution_mode":"MANUAL_ONLY","candidates":candidates}))
            resolutions.write_text("\n".join(resolution_rows)+"\n",encoding="utf-8")
            report=resolved_evidence_report(evidence,resolutions,minimum_sample=6)
            comparison=next(row for row in report["benchmark_comparisons"] if row["agent"]=="S")
            self.assertEqual((comparison["asset_coverage"],comparison["assets_meeting_minimum"]),(3,3))
            self.assertGreater(comparison["worst_asset_brier_lower_95"],0)
            decision=report["admission_governance"][0]
            self.assertEqual(decision["status"],"ELIGIBLE_FOR_HUMAN_REVIEW")
            self.assertFalse(decision["automatic_promotion"])

    def test_consensus_counts_correlation_groups_not_duplicate_agents(self):
        agents=(StaticAgent("A","CORRELATED",static_candidate("A",Side.YES,.9)),
                StaticAgent("B","CORRELATED",static_candidate("B",Side.YES,.7)),
                StaticAgent("C","INDEPENDENT",static_candidate("C",Side.YES,.6)))
        result=MarketSupervisor(agents).evaluate(AgentContext(NOW,"KALSHI","BTC-USD",{}))
        summary=result.consensus[0]
        self.assertEqual((summary.contributor_count,summary.independent_group_count),(3,2))
        self.assertAlmostEqual(summary.probability_yes,.7)
        self.assertEqual(summary.status,"AGREEMENT_DIAGNOSTIC")
        self.assertFalse(summary.actionable)

    def test_material_cross_side_disagreement_forces_consensus_abstention(self):
        agents=(StaticAgent("A","G1",static_candidate("A",Side.YES,.9)),
                StaticAgent("B","G2",static_candidate("B",Side.NO,.9)))
        summary=MarketSupervisor(agents).evaluate(AgentContext(NOW,"KALSHI","BTC-USD",{})).consensus[0]
        self.assertEqual(summary.status,"ABSTAIN_DISAGREEMENT")
        self.assertEqual(summary.side,Side.ABSTAIN)
        self.assertFalse(summary.actionable)

    def test_append_only_event_sink(self):
        row = {"asset": "BTC", "side": "YES", "confidence": .9,
               "conservative_probability": .85, "ask": "0.75", "economically_valid": True}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.jsonl"
            supervisor = MarketSupervisor((MantisAdapter(),), events=JsonlEventSink(path))
            supervisor.evaluate(context(row)); supervisor.evaluate(context(row))
            self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 2)
            replay = EvidenceReplay(path).summary()
            self.assertEqual((replay.evaluations, replay.candidates), (2, 2))
            self.assertEqual((replay.reviewable, replay.blocked), (2, 0))

    def test_event_ids_are_restart_idempotent_and_final_truncation_recovers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            event = AuditEvent("fixed", "TEST", NOW, "run", {})
            JsonlEventSink(path).append(event)
            JsonlEventSink(path).append(event)
            with path.open("a", encoding="utf-8") as handle:
                handle.write('{"truncated"')
            self.assertEqual(len(read_events(path)), 1)

    def test_calibration_is_report_only_and_sample_gated(self):
        small = [CalibrationObservation(.8, True, "MANTIS", V22_POLICY.version),
                 CalibrationObservation(.2, False, "MANTIS", V22_POLICY.version)]
        report = evaluate_calibration(small, bin_count=5, minimum_sample=30)
        self.assertEqual(report.status, "INSUFFICIENT_EVIDENCE")
        self.assertAlmostEqual(report.brier_score, .04)
        large = evaluate_calibration(small * 15, bin_count=5, minimum_sample=30)
        self.assertEqual(large.status, "REPORT_ONLY")
        self.assertEqual(large.sample_size, 30)

    def test_command_center_publish_is_explicit(self):
        class State:
            value = None
            def set_svi(self, value): self.value = value
        row = {"asset": "BTC", "side": "YES", "confidence": .9,
               "conservative_probability": .85, "ask": "0.75", "economically_valid": True}
        result = MarketSupervisor((MantisAdapter(),)).evaluate(context(row))
        state = State()
        published = publish_to_mantis(state, result)
        self.assertIs(state.value, published)
        self.assertTrue(published["observation_only"])

    def test_live_manual_runner_wires_svi_after_authoritative_scan(self):
        source = Path("mantis_v4_live.py").read_text(encoding="utf-8")
        scan = source.index("snapshots,selection,persisted=engine.scan")
        evaluate = source.index("svi_result=svi.evaluate", scan)
        publish = source.index("publish_to_mantis(state,svi_result,svi_evidence_report)", evaluate)
        self.assertLess(scan, evaluate)
        self.assertLess(evaluate, publish)
        self.assertIn('signal_path/"svi"/"events.jsonl"', source)
        self.assertIn("NullEventSink() if diagnostic", source)
        self.assertIn('state.log("WARNING","SVI","SUPERVISOR DEGRADED"', source)
        self.assertIn("KalshiMarketImpliedBenchmark()",source)

    def test_verified_resolution_join_uses_latest_forecast_and_converts_no_probability(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); evidence = root / "events.jsonl"; resolutions = root / "resolutions.jsonl"
            sink = JsonlEventSink(evidence)
            def event(event_id, stamp, confidence):
                return AuditEvent(event_id, "SUPERVISOR_EVALUATION", stamp, "run", {"candidates":[{
                    "contract_id":"BTC|window|15m","agent":"MANTIS","policy_version":V22_POLICY.version,
                    "instrument":"BTC-USD","side":"NO","probability":confidence}]})
            sink.append(event("one", NOW, .70))
            sink.append(event("two", NOW.replace(minute=1), .80))
            resolutions.write_text('{"contract_id":"BTC|window|15m","result":"NO","resolution_status":"VERIFIED","resolved_at_utc":"2026-08-19T12:15:00+00:00","settlement_source":"CF_BENCHMARKS"}\n',encoding="utf-8")
            joined, unresolved = join_verified_forecasts(evidence, resolutions)
            self.assertEqual((len(joined), unresolved), (1, 0))
            self.assertAlmostEqual(joined[0].probability_yes, .20)
            self.assertTrue(joined[0].outcome_yes is False)
            report = resolved_evidence_report(evidence, resolutions, minimum_sample=1)
            self.assertEqual(report["groups"][0]["status"], "REPORT_ONLY")
            self.assertFalse(report["model_activation"])

    def test_post_resolution_forecast_is_never_joined(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence=Path(tmp)/"events.jsonl"; resolutions=Path(tmp)/"resolutions.jsonl"
            sink=JsonlEventSink(evidence)
            candidate={"agent":"S","policy_version":"P","contract_id":"BTC|window|15m",
                "instrument":"BTC-USD","side":"YES","probability":.99,"role":"SHADOW",
                "rank":None,"risk_disposition":"SHADOW_ONLY"}
            sink.append(AuditEvent("late","SUPERVISOR_EVALUATION",
                NOW.replace(minute=16),"run",{"execution_mode":"MANUAL_ONLY","candidates":[candidate]}))
            resolutions.write_text('{"contract_id":"BTC|window|15m","result":"YES","resolution_status":"VERIFIED","resolved_at_utc":"2026-08-19T12:15:00+00:00","settlement_source":"CF_BENCHMARKS"}\n',encoding="utf-8")
            joined,_=join_verified_forecasts(evidence,resolutions)
            self.assertFalse(joined)

    def test_unverified_resolution_is_never_scored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); evidence=root/"events.jsonl"; resolutions=root/"resolutions.jsonl"
            row={"asset":"BTC","side":"YES","confidence":.9,"conservative_probability":.85,
                 "ask":".75","economically_valid":True,"contract_id":"BTC|window|15m"}
            supervisor=MarketSupervisor((MantisAdapter(),),events=JsonlEventSink(evidence))
            supervisor.evaluate(context(row))
            resolutions.write_text('{"contract_id":"BTC|window|15m","result":"YES","resolution_status":"MISMATCH"}\n',encoding="utf-8")
            joined, unresolved=join_verified_forecasts(evidence,resolutions)
            self.assertFalse(joined); self.assertEqual(unresolved,1)

    def test_forward_view_contains_dedicated_svi_evidence_section(self):
        source=Path("mantis_v4/ui/web/modules.js").read_text(encoding="utf-8")
        self.assertIn('SAAF VENTURES INTELLIGENCE',source)
        self.assertIn('RESOLUTION REQUIREMENT',source)
        self.assertIn('MODEL ACTIVATION',source)
        self.assertIn('EXPECTED CALIBRATION ERROR',source)
        self.assertIn('SPECIALIST REGISTRY',source)
        self.assertIn('ABSTAIN_DISAGREEMENT',source)
        self.assertIn('MARKET-IMPLIED BENCHMARK',source)
        self.assertIn('BRIER / LOG-LOSS IMPROVEMENT',source)

    def test_operational_audit_and_manifest_are_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); evidence=root/"events.jsonl"; resolutions=root/"resolutions.jsonl"
            row={"asset":"BTC","side":"YES","confidence":.9,"conservative_probability":.85,
                 "ask":".75","economically_valid":True,"contract_id":"BTC|window|15m"}
            MarketSupervisor((MantisAdapter(),),events=JsonlEventSink(evidence)).evaluate(context(row))
            before=evidence.read_bytes()
            audit=audit_evidence(evidence,resolutions); manifest=evidence_manifest(evidence,resolutions)
            report=operational_report(evidence,resolutions)
            self.assertEqual(audit["status"],"PASS")
            self.assertEqual(manifest["schema_versions"],[5])
            self.assertEqual(manifest["agents"],["MANTIS"])
            self.assertEqual(report["audit"]["status"],"PASS")
            self.assertEqual(evidence.read_bytes(),before)

    def test_legacy_v1_without_contract_identity_is_warning_not_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence=Path(tmp)/"events.jsonl"
            event=AuditEvent("legacy","SUPERVISOR_EVALUATION",NOW,"run",{
                "execution_mode":"MANUAL_ONLY","candidates":[{"agent":"MANTIS","policy_version":"OLD"}]},1)
            JsonlEventSink(evidence).append(event)
            audit=audit_evidence(evidence,Path(tmp)/"resolutions.jsonl")
            self.assertEqual(audit["status"],"PASS")
            self.assertIn("LEGACY_V1_CANDIDATE_WITHOUT_CONTRACT_ID",audit["warnings"])

    def test_v2_evidence_without_registry_remains_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence=Path(tmp)/"events.jsonl"
            event=AuditEvent("v2","SUPERVISOR_EVALUATION",NOW,"run",{
                "execution_mode":"MANUAL_ONLY","candidates":[{"contract_id":"BTC|window|15m",
                "agent":"MANTIS","policy_version":"V2","side":"YES","probability":.8}]},2)
            JsonlEventSink(evidence).append(event)
            self.assertEqual(audit_evidence(evidence,Path(tmp)/"resolutions.jsonl")["status"],"PASS")

    def test_svi_cli_modes_are_exposed(self):
        from mantis_v4_live import build_parser
        parser=build_parser()
        self.assertTrue(parser.parse_args(["--svi-report"]).svi_report)
        self.assertTrue(parser.parse_args(["--svi-audit"]).svi_audit)
        self.assertTrue(parser.parse_args(["--svi-manifest"]).svi_manifest)


if __name__ == "__main__":
    unittest.main()
