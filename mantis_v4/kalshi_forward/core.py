"""Step 9 Kalshi forward-shadow records, audit, reporting, and simulation.

This package deliberately has no dependency on the live selector, UI, alerts,
voice, economics, broker credentials, or execution code.  It records causal
research observations and later official outcomes in separate JSONL streams.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
import threading
import uuid
from collections import deque
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from mantis_v4.economics.kalshi import SERIES_BY_ASSET
from mantis_v4.reference.kalshi_reference_risk import assess_reference_risk

SCHEMA_VERSION = "KALSHI_FORWARD_SHADOW_V1"
MODEL_POLICY = "KALSHI_REFERENCE_V1:TERMINAL_PRICE_CONSERVATIVE_PROXY_NO_SQRT60"
REFERENCE_POLICY = "KALSHI_REFERENCE_RISK_V1_SHADOW"
ACTIONABILITY = "SHADOW_ONLY"
SAMPLE_LABEL = "NEW_CAUSAL_FORWARD_SAMPLE"
ASSETS = tuple(SERIES_BY_ASSET)
SHADOW_POLICIES = (
    "BASE_TRANSFERRED", "FIXED_2BP", "FIXED_5BP", "FIXED_10BP",
    "DEV_P95", "DEV_P99",
)
STREAM_KEYS = {
    "runs": "run_record_id", "observations": "observation_id",
    "shadow_candidates": "candidate_id", "resolutions": "resolution_id",
    "provider_health": "provider_health_id", "audit_events": "audit_event_id",
}


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("timezone-aware UTC timestamp required")
    return value.astimezone(UTC).isoformat()


def _hash(*parts: Any) -> str:
    return hashlib.sha256("|".join(str(x) for x in parts).encode("utf-8")).hexdigest()


def deterministic_contract_id(asset: str, start: datetime, end: datetime) -> str:
    if asset not in ASSETS or end - start != timedelta(minutes=15):
        raise ValueError("exact supported 15-minute asset/window required")
    return f"{asset}|{_iso(start)}|{_iso(end)}|15m"


def _json_value(value: Any):
    if isinstance(value, Decimal): return str(value)
    if isinstance(value, datetime): return _iso(value)
    if is_dataclass(value): return _json_value(asdict(value))
    if isinstance(value, dict): return {str(k): _json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [_json_value(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value): return None
    return value


class ShadowStore:
    """Small append-only JSONL store with restart-safe deterministic dedupe."""
    def __init__(self, root: Path, recent_id_limit: int | None = None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.recent_id_limit = int(recent_id_limit) if recent_id_limit else None
        self._ids = {name: {row.get(key) for row in self.read(name, limit=self.recent_id_limit) if row.get(key)}
                     for name, key in STREAM_KEYS.items()}
        self._counts={name:0 for name in STREAM_KEYS}; self._last={name:None for name in STREAM_KEYS}; self._eligible=0
        for name in STREAM_KEYS:
            path=self.path(name)
            if not path.exists(): continue
            with path.open("r",encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip(): continue
                    try: row=json.loads(line)
                    except json.JSONDecodeError: continue
                    if isinstance(row,dict):
                        self._counts[name]+=1; self._last[name]=row
                        if name=="shadow_candidates" and row.get("original_model_qualified"): self._eligible+=1

    def path(self, stream: str) -> Path:
        if stream not in STREAM_KEYS: raise ValueError(f"unknown shadow stream {stream}")
        return self.root / f"{stream}.jsonl"

    def read(self, stream: str, limit: int | None = None) -> list[dict]:
        path = self.path(stream)
        if not path.exists(): return []
        rows = [] if not limit else deque(maxlen=int(limit))
        with path.open("r", encoding="utf-8") as handle:
            pending=None; line_number=0
            for current in handle:
                if pending is None: pending=current; continue
                line_number+=1; line=pending; pending=current
                if not line.strip(): continue
                try: row = json.loads(line)
                except json.JSONDecodeError:
                    raise ValueError(f"malformed {stream}.jsonl line {line_number}")
                if not isinstance(row, dict): raise ValueError(f"non-object {stream} record")
                rows.append(row)
            if pending and pending.strip():
                try: row=json.loads(pending)
                except json.JSONDecodeError: row=None  # tolerate only a partial final line
                if row is not None:
                    if not isinstance(row,dict): raise ValueError(f"non-object {stream} final record")
                    rows.append(row)
        return list(rows)

    def append(self, stream: str, record: dict) -> bool:
        key = STREAM_KEYS[stream]; identifier = record.get(key)
        if not identifier: raise ValueError(f"{key} required")
        payload = _json_value(record)
        with self._lock:
            if identifier in self._ids[stream]: return False
            with self.path(stream).open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
                handle.flush(); os.fsync(handle.fileno())
            self._ids[stream].add(identifier)
            self._counts[stream]+=1; self._last[stream]=payload
            if stream=="shadow_candidates" and payload.get("original_model_qualified"): self._eligible+=1
            if self.recent_id_limit and len(self._ids[stream]) > self.recent_id_limit:
                self._ids[stream] = {row.get(key) for row in self.read(stream, limit=self.recent_id_limit) if row.get(key)}
            return True

    def summary(self) -> dict:
        """Constant-memory live counters for the read-only command center."""
        unresolved=max(0,self._counts["shadow_candidates"]-self._counts["resolutions"])
        latest=self._last.get("observations") or {}
        resolution=self._last.get("resolutions") or {}
        return {"mode":"FORWARD_SHADOW_ONLY","policy":REFERENCE_POLICY,
            "sample_label":SAMPLE_LABEL,"observations":self._counts["observations"],
            "resolutions":self._counts["resolutions"],"eligible":self._eligible,
            "shadow_candidates":self._counts["shadow_candidates"],"unresolved":unresolved,
            "last_observation_utc":latest.get("observed_at_utc"),
            "last_resolved_window":resolution.get("window_end_utc"),
            "provider_health_records":self._counts["provider_health"],
            "data_directory":str(self.root),"status":"EXPERIMENTAL_NOT_YET_FORWARD_VALIDATED"}


def common_envelope(*, run_id: str, contract_id: str, asset: str, mapping,
                    observed_at: datetime) -> dict:
    return {
        "schema_version": SCHEMA_VERSION, "policy_version": REFERENCE_POLICY,
        "model_policy": MODEL_POLICY, "reference_policy": REFERENCE_POLICY,
        "actionability": ACTIONABILITY, "sample_label": SAMPLE_LABEL,
        "run_id": run_id, "contract_id": contract_id, "asset": asset,
        "series_ticker": mapping.series_ticker, "event_ticker": mapping.event_ticker,
        "market_ticker": mapping.market_ticker,
        "window_start_utc": _iso(mapping.window_start_utc),
        "window_end_utc": _iso(mapping.window_end_utc),
        "observed_at_utc": _iso(observed_at),
    }


def original_qualified(snapshot) -> bool:
    return bool(snapshot.seconds_remaining <= 300 and snapshot.p_yes >= .95 or
                snapshot.seconds_remaining <= 300 and snapshot.p_yes <= .05) and bool(
        snapshot.conservative_bound >= .90 and snapshot.fragility <= 50 and
        snapshot.disagreement <= .05 and snapshot.crossing_probability <= .35 and
        snapshot.reference_crossings <= 4)


def build_observation(*, run_id: str, snapshot, mapping, quote,
                      empirical_bounds: dict[str, dict[str, Decimal]]) -> tuple[dict, dict]:
    observed = datetime.fromisoformat(snapshot.timestamp_utc)
    if snapshot.asset not in ASSETS or mapping.asset != snapshot.asset:
        raise ValueError("asset/mapping mismatch")
    contract_id = deterministic_contract_id(snapshot.asset, mapping.window_start_utc, mapping.window_end_utc)
    if observed > mapping.window_end_utc or mapping.target <= 0:
        raise ValueError("non-causal or invalid mapping")
    expected_remaining=(mapping.window_end_utc-observed).total_seconds()
    if abs(expected_remaining-float(snapshot.seconds_remaining)) > 2.0:
        raise ValueError("snapshot does not belong to verified current window")
    fixed = {name: assess_reference_risk(asset=snapshot.asset, target=mapping.target,
              proxy_current=Decimal(str(snapshot.current_price)), uncertainty_bound_bps=Decimal(str(bound))).classification
             for name, bound in (("fixed_1bp", 1), ("fixed_2bp", 2), ("fixed_5bp", 5), ("fixed_10bp", 10))}
    bounds = empirical_bounds.get(snapshot.asset)
    if not bounds or not all(x in bounds for x in ("P50", "P95", "P99")):
        empirical = {"dev_p50": "REFERENCE_UNKNOWN", "dev_p95": "REFERENCE_UNKNOWN", "dev_p99": "REFERENCE_UNKNOWN"}
    else:
        empirical = {f"dev_{q.lower()}": assess_reference_risk(asset=snapshot.asset, target=mapping.target,
                     proxy_current=Decimal(str(snapshot.current_price)), uncertainty_bound_bps=Decimal(str(bounds[q]))).classification
                     for q in ("P50", "P95", "P99")}
    decision_key = f"{snapshot.asset}|{mapping.window_end_utc.isoformat()}|{int(snapshot.seconds_remaining)}"
    envelope = common_envelope(run_id=run_id, contract_id=contract_id, asset=snapshot.asset,
                               mapping=mapping, observed_at=observed)
    quote_fields = _quote_fields(quote)
    observation = {**envelope, "observation_id": _hash(REFERENCE_POLICY, decision_key),
        "decision_point_id": decision_key, "kalshi_target": str(mapping.target),
        "event_reference_provenance": "KALSHI_CRYPTO15M_CF_BENCHMARKS",
        "mapping_settlement_provenance": mapping.settlement_reference_provenance,
        "proxy_current_value": snapshot.current_price, "current_value_provenance": "YAHOO_PROXY",
        "distance_from_target_bps": str(Decimal("10000") *
            (Decimal(str(snapshot.current_price))-mapping.target)/mapping.target),
        "model_probability_yes": snapshot.p_yes, "model_probability_selected_side": max(snapshot.p_yes, snapshot.p_no),
        "model_side": snapshot.predicted_side, "conservative_probability": snapshot.conservative_bound,
        "fragility": snapshot.fragility, "disagreement": snapshot.disagreement,
        "crossing_probability": snapshot.crossing_probability, "crossing_count": snapshot.reference_crossings,
        "seconds_remaining": snapshot.seconds_remaining, "volatility_estimate": snapshot.volatility_estimate,
        "volatility_regime": getattr(snapshot,"volatility_regime","UNKNOWN"),
        "original_model_qualified": original_qualified(snapshot),
        "reference_risk": {**fixed, **empirical}, "quote": quote_fields,
        "fee_metadata": {"fee_type": None, "fee_multiplier": None,
            "fee_schedule_version": None, "fee_provenance": "UNAVAILABLE_NOT_REQUIRED_FOR_REFERENCE_VALIDATION"},
        "future_fields_present": False}
    qualified = observation["original_model_qualified"]
    policies = {
        "BASE_TRANSFERRED": qualified,
        "FIXED_2BP": qualified and fixed["fixed_2bp"] == "REFERENCE_ROBUST",
        "FIXED_5BP": qualified and fixed["fixed_5bp"] == "REFERENCE_ROBUST",
        "FIXED_10BP": qualified and fixed["fixed_10bp"] == "REFERENCE_ROBUST",
        "DEV_P95": qualified and empirical["dev_p95"] == "REFERENCE_ROBUST",
        "DEV_P99": qualified and empirical["dev_p99"] == "REFERENCE_ROBUST",
    }
    candidate = {**envelope, "candidate_id": _hash("candidate", observation["observation_id"]),
                 "observation_id": observation["observation_id"], "shadow_policy_results": policies,
                 "would_have_done": {k: "SHADOW_QUALIFY" if v else "SHADOW_WAIT" for k, v in policies.items()},
                 "production_authorization": False}
    return observation, candidate


def _quote_fields(quote) -> dict:
    if quote is None:
        return {"status": "UNAVAILABLE", "provider": "KALSHI_PUBLIC_REST", "authenticated": False}
    return {"status": "READY" if quote.quote_verified else "UNVERIFIED", "provider": quote.provider,
        "authenticated": quote.authenticated, "yes_bid": quote.yes_bid, "yes_ask": quote.yes_ask,
        "yes_ask_size": quote.yes_ask_size, "no_bid": quote.no_bid, "no_ask": quote.no_ask,
        "no_ask_size": quote.no_ask_size, "quote_received_at_utc": quote.received_at_utc,
        "quote_age_seconds": quote.quote_age_seconds,
        "timestamp_provenance": quote.quote_timestamp_provenance}


def build_resolution(*, run_id: str, observation: dict, market: dict,
                     resolved_at: datetime) -> dict:
    result = str(market.get("result") or "").lower()
    if result not in {"yes", "no"}: raise ValueError("official finalized result unavailable")
    target = Decimal(str(market.get("floor_strike")))
    expiration = Decimal(str(market.get("expiration_value")))
    decimals = 2 if observation["asset"] in {"BTC-USD", "ETH-USD"} else 4
    computed = "yes" if expiration.quantize(Decimal(1).scaleb(-decimals)) >= target else "no"
    status = "VERIFIED" if computed == result else "MISMATCH"
    return {**{k: observation[k] for k in ("schema_version", "policy_version", "model_policy",
        "reference_policy", "actionability", "sample_label", "contract_id", "asset",
        "series_ticker", "event_ticker", "market_ticker", "window_start_utc", "window_end_utc")},
        "run_id": run_id, "observation_run_id": observation["run_id"],
        "observed_at_utc": observation["observed_at_utc"],
        "resolution_id": _hash("resolution", observation["contract_id"]),
        "result": result.upper(), "expiration_value": str(expiration), "floor_strike": str(target),
        "settlement_source": "CF_BENCHMARKS", "resolved_at_utc": _iso(resolved_at),
        "resolution_status": status, "comparison_operator": ">=", "official_market_status": market.get("status")}


def audit_store(store: ShadowStore, *, now: datetime | None = None) -> dict:
    now = (now or datetime.now(UTC)).astimezone(UTC); errors = []
    rows = {name: store.read(name) for name in STREAM_KEYS}
    for stream, key in STREAM_KEYS.items():
        ids = [r.get(key) for r in rows[stream]]
        if len(ids) != len(set(ids)): errors.append(f"DUPLICATE_ID:{stream}")
        for row in rows[stream]:
            if row.get("actionability") not in {None, ACTIONABILITY}: errors.append(f"ACTIONABILITY:{stream}")
            if row.get("asset") not in {None, "SYSTEM", *ASSETS}: errors.append(f"ASSET:{stream}")
            if any(k in row for k in ("order_id", "voice_event", "primary_selection")): errors.append(f"FORBIDDEN_FIELD:{stream}")
    observations = rows["observations"]
    by_contract = {o["contract_id"]: o for o in observations}
    for o in observations:
        observed = datetime.fromisoformat(o["observed_at_utc"]); end = datetime.fromisoformat(o["window_end_utc"])
        if observed > now or observed > end: errors.append("FUTURE_OBSERVATION")
        start=datetime.fromisoformat(o["window_start_utc"])
        if end-start != timedelta(minutes=15) or start.minute%15 or end.minute%15: errors.append("WINDOW_MAPPING")
        if o.get("series_ticker") != SERIES_BY_ASSET.get(o.get("asset")): errors.append("SERIES_MAPPING")
        if o.get("policy_version") != REFERENCE_POLICY or o.get("actionability") != ACTIONABILITY: errors.append("POLICY_IDENTITY")
        if o.get("event_reference_provenance") != "KALSHI_CRYPTO15M_CF_BENCHMARKS": errors.append("TARGET_PROVENANCE")
    for r in rows["resolutions"]:
        o = by_contract.get(r.get("contract_id"))
        if not o: errors.append("ORPHAN_RESOLUTION"); continue
        if datetime.fromisoformat(r["resolved_at_utc"]) < datetime.fromisoformat(o["observed_at_utc"]): errors.append("EARLY_RESOLUTION")
        if r.get("resolution_status") != "VERIFIED": errors.append("RESOLUTION_MISMATCH")
    return {"status": "PASS" if not errors else "FAIL", "errors": sorted(set(errors)),
        "counts": {k: len(v) for k, v in rows.items()},
        "distinct_windows": len({o.get("window_end_utc") for o in observations}),
        "actionable_selections": 0, "actionable_voice_events": 0, "orders_submitted": 0}


def shadow_report(store: ShadowStore) -> dict:
    observations = store.read("observations"); candidates = store.read("shadow_candidates")
    resolutions = {r["contract_id"]: r for r in store.read("resolutions") if r.get("resolution_status") == "VERIFIED"}
    by_obs = {o["observation_id"]: o for o in observations}; report = {}
    for policy in SHADOW_POLICIES:
        matured = [(by_obs[c["observation_id"]], resolutions.get(c["contract_id"])) for c in candidates
                   if c["shadow_policy_results"].get(policy) and c["observation_id"] in by_obs and c["contract_id"] in resolutions]
        correct = []; probabilities=[]; outcomes=[]
        for o, r in matured:
            correct.append(o["model_side"] == r["result"]); probabilities.append(float(o["model_probability_yes"])); outcomes.append(r["result"]=="YES")
        report[policy] = {"eligible_resolved": sum(c["contract_id"] in resolutions for c in candidates),
            "shadow_qualified_resolved": len(matured),
            "coverage": len(matured)/sum(c["contract_id"] in resolutions for c in candidates) if candidates and sum(c["contract_id"] in resolutions for c in candidates) else None,
            "accuracy": sum(correct)/len(correct) if correct else None,
            "brier": sum((p-y)**2 for p,y in zip(probabilities,outcomes))/len(matured) if matured else None,
            "log_loss": _log_loss(probabilities,outcomes),
            "yes_accuracy": _accuracy_side(matured, "YES"), "no_accuracy": _accuracy_side(matured, "NO"),
            "per_asset": {a: _accuracy_asset(matured, a) for a in ASSETS},
            "calibration": _calibration(probabilities,outcomes),
            "time_to_close": _group_accuracy(matured,lambda o: _time_bucket(float(o["seconds_remaining"]))),
            "volatility_regime": _group_accuracy(matured,lambda o: o.get("volatility_regime") or "UNSPECIFIED"),
            "distance_to_target": _group_accuracy(matured,lambda o: _distance_bucket(abs(float(o["distance_from_target_bps"])))),
            "window_cluster_bootstrap_ci95": _window_bootstrap(matured)}
    windows = len({o["window_end_utc"] for o in observations})
    return {"sample_label": SAMPLE_LABEL, "resolved_asset_contract_observations": len(resolutions),
            "distinct_windows": windows, "next_milestone": next((x for x in (50,100,250,500,1000,2000) if len(resolutions)<x), None),
            "policies": report, "cluster_note": "asset rows sharing window are not independent"}


def _accuracy_side(rows, side):
    block = [(o, r) for o, r in rows if o["model_side"] == side]
    return {"n": len(block), "accuracy": sum(o["model_side"] == r["result"] for o,r in block)/len(block) if block else None}


def _accuracy_asset(rows, asset):
    block = [(o,r) for o,r in rows if o["asset"] == asset]
    return {"n": len(block), "accuracy": sum(o["model_side"] == r["result"] for o,r in block)/len(block) if block else None}


def _log_loss(probabilities,outcomes):
    if not probabilities: return None
    return sum(-(y*math.log(min(.999999,max(.000001,p)))+(1-y)*math.log(min(.999999,max(.000001,1-p)))) for p,y in zip(probabilities,outcomes))/len(probabilities)


def _calibration(probabilities,outcomes):
    bins=[]
    for low,high in ((0,.1),(.1,.25),(.25,.5),(.5,.75),(.75,.9),(.9,1.000001)):
        rows=[(p,y) for p,y in zip(probabilities,outcomes) if low<=p<high]
        bins.append({"range":f"{low:.2f}-{min(high,1):.2f}","n":len(rows),
                     "mean_probability":sum(p for p,_ in rows)/len(rows) if rows else None,
                     "empirical_yes_rate":sum(y for _,y in rows)/len(rows) if rows else None})
    return bins


def _time_bucket(seconds):
    if seconds<=60:return "T_0_60"
    if seconds<=120:return "T_60_120"
    if seconds<=180:return "T_120_180"
    if seconds<=240:return "T_180_240"
    if seconds<=300:return "T_240_300"
    return "PRE_T300"


def _distance_bucket(value):
    if value<1:return "LT_1BP"
    if value<2:return "1_2BP"
    if value<5:return "2_5BP"
    if value<10:return "5_10BP"
    return "GE_10BP"


def _group_accuracy(rows,key):
    grouped={}
    for o,r in rows: grouped.setdefault(key(o),[]).append(o["model_side"]==r["result"])
    return {k:{"n":len(v),"accuracy":sum(v)/len(v)} for k,v in sorted(grouped.items())}


def _window_bootstrap(rows,iterations=400):
    windows={}
    for o,r in rows: windows.setdefault(o["window_end_utc"],[]).append(o["model_side"]==r["result"])
    if len(windows)<2:return None
    blocks=list(windows.values()); rng=random.Random(911); values=[]
    for _ in range(iterations):
        sample=[blocks[rng.randrange(len(blocks))] for _ in blocks]; flat=[x for block in sample for x in block]
        values.append(sum(flat)/len(flat))
    values.sort(); return {"lower":values[int(.025*iterations)],"upper":values[min(iterations-1,int(.975*iterations))],"clusters":len(blocks)}


def render_audit(result: dict) -> str:
    lines = ["KALSHI FORWARD SHADOW AUDIT", f"RESULT .................. {result['status']}"]
    lines += [f"{k.upper():25} {v}" for k,v in result["counts"].items()]
    lines += [f"DISTINCT WINDOWS ........ {result['distinct_windows']}",
              "ACTIONABLE SELECTIONS ... 0", "ACTIONABLE VOICE ........ 0", "ORDERS SUBMITTED ........ 0"]
    if result["errors"]: lines += ["ERRORS:"] + [f"- {e}" for e in result["errors"]]
    return "\n".join(lines)


def simulate_shadow(root: Path, windows: int = 250, *, restart_at: int = 125) -> dict:
    """Deterministic accelerated lifecycle proving isolation and dedupe."""
    from types import SimpleNamespace
    if windows < 1: raise ValueError("positive windows required")
    store = ShadowStore(root); rng = random.Random(901); start = datetime(2026, 1, 1, tzinfo=UTC)
    bounds = {a: {"P50": Decimal("2"), "P95": Decimal("7"), "P99": Decimal("12")} for a in ASSETS}
    run_id = "SIMULATION_STEP9"; attempted = 0
    for w in range(windows):
        if w == restart_at: store = ShadowStore(root)
        ws = start + timedelta(minutes=15*w); we = ws + timedelta(minutes=15)
        for i, asset in enumerate(ASSETS):
            attempted += 1; target = Decimal(str(100 + i*10 + w/100))
            mapping = SimpleNamespace(asset=asset, series_ticker=SERIES_BY_ASSET[asset], event_ticker=f"E{w}",
                market_ticker=f"M{w}-{i}", window_start_utc=ws, window_end_utc=we, target=target,
                settlement_reference_provenance="KALSHI_CRYPTO15M_RULES")
            p = .96 if (w+i)%3 else .04; spot = float(target)*(1 + ((1 if p>=.5 else -1)*(3+i))/10000)
            snap = SimpleNamespace(asset=asset, timestamp_utc=(we-timedelta(seconds=240)).isoformat(), current_price=spot,
                p_yes=p, p_no=1-p, predicted_side="YES" if p>=.5 else "NO", conservative_bound=.92,
                fragility=20., disagreement=.01, crossing_probability=.1, reference_crossings=1,
                seconds_remaining=240., volatility_estimate=.002)
            obs, cand = build_observation(run_id=run_id,snapshot=snap,mapping=mapping,quote=None,empirical_bounds=bounds)
            store.append("observations",obs); store.append("shadow_candidates",cand)
            # Deliberately append twice: restart/dedupe invariant must hold.
            store.append("observations",obs)
            result = snap.predicted_side.lower() if rng.random()>.08 else ("no" if snap.predicted_side=="YES" else "yes")
            expiration = target + (Decimal("0.01") if result=="yes" else Decimal("-0.01"))
            resolution = build_resolution(run_id=run_id,observation=obs,market={"result":result,
                "expiration_value":str(expiration),"floor_strike":str(target),"status":"settled"},resolved_at=we+timedelta(seconds=5))
            store.append("resolutions",resolution)
    audit = audit_store(store, now=start+timedelta(days=10))
    return {"windows": windows, "asset_attempts": attempted, "observations": len(store.read("observations")),
            "resolutions": len(store.read("resolutions")), "audit": audit,
            "actionable_selections": 0, "actionable_voice_events": 0, "orders_submitted": 0}
