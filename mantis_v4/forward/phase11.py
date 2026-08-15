"""Phase 11 forward-observation analytics. Read-only except explicit event helpers."""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np

from ..models.evaluation import clustered_accuracy
from .store import ForwardStore

UTC = timezone.utc
POLICY_IDENTIFIER = "H_p0.95_l0.90_f50_d.05_t300"
HISTORICAL = {"entries": 2135, "coverage": .6373, "accuracy": .9658,
              "ci_low": .9564, "ci_high": .9757,
              "label": "HISTORICAL PROXY CLASSIFICATION"}
TIMING_BUCKETS = ((240, 300, "T-300 TO T-240"), (180, 240, "T-240 TO T-180"),
                  (120, 180, "T-180 TO T-120"), (60, 120, "T-120 TO T-60"),
                  (30, 60, "T-60 TO T-30"), (0, 30, "T-30 TO RESOLUTION"))
CONFIDENCE_BUCKETS = ((.50, .60, "0.50-0.60"), (.60, .70, "0.60-0.70"),
                      (.70, .80, "0.70-0.80"), (.80, .90, "0.80-0.90"),
                      (.90, .95, "0.90-0.95"), (.95, .975, "0.95-0.975"),
                      (.975, 1.000001, "0.975-1.00"))
MILESTONES = (25, 50, 100, 250, 500, 1000)


def _key(row): return row.get("contract_id"), row.get("asset")
def _window_group(row): return row.get("window_start") or row.get("contract_id")


def _bucket(value, definitions):
    for low, high, label in definitions:
        if low <= value < high:
            return label
    return "OUTSIDE RANGE"


def _safe_mean(values):
    values = [float(v) for v in values if isinstance(v, (int, float)) and math.isfinite(v)]
    return sum(values) / len(values) if values else None


def _losses(rows):
    if not rows:
        return {"n": 0, "brier_score": None, "log_loss": None}
    probabilities = np.clip(np.asarray([r["probability"] for r in rows], float), 1e-12, 1-1e-12)
    outcomes = np.asarray([r["outcome"] for r in rows], float)
    return {"n": len(rows), "brier_score": float(np.mean((probabilities-outcomes)**2)),
            "log_loss": float(np.mean(-(outcomes*np.log(probabilities)+(1-outcomes)*np.log(1-probabilities))))}


def _resolved_entries(store):
    resolutions = {_key(r): r for r in store.read("resolutions")}
    observations = {o.get("observation_id"): o for o in store.read("observations")}
    rows = []
    for entry in store.read("entries"):
        resolution = resolutions.get(_key(entry))
        if not resolution or resolution.get("classification_correct") is None:
            continue
        row = {**entry, **resolution}
        observation = observations.get(entry.get("observation_id"), {})
        row["window_start"] = observation.get("window_start")
        row["reference_status"] = entry.get("reference_status", observation.get("reference_status", "UNKNOWN"))
        row["provider_health"] = entry.get("provider_health", observation.get("metadata", {}).get("quality_reason", "UNKNOWN"))
        rows.append(row)
    return rows


def _stats(rows, *, n_boot=400):
    if not rows:
        return {"n": 0, "correct": 0, "incorrect": 0, "accuracy": None,
                "ci_low": None, "ci_high": None, "distinct_windows": 0}
    correct = np.asarray([bool(r["classification_correct"]) for r in rows], float)
    groups = np.asarray([_window_group(r) for r in rows])
    interval = clustered_accuracy(correct, groups, n_boot=n_boot)
    enough = len(rows) >= 25 and interval.n_groups >= 10
    return {"n": len(rows), "correct": int(correct.sum()), "incorrect": int(len(rows)-correct.sum()),
            "accuracy": float(correct.mean()), "ci_low": float(interval.low) if enough and math.isfinite(interval.low) else None,
            "ci_high": float(interval.high) if enough and math.isfinite(interval.high) else None,
            "distinct_windows": int(interval.n_groups), "sample_status": "REPORTABLE" if enough else "SAMPLE TOO SMALL"}


def _calibration(rows):
    grouped = {label: [] for _, _, label in CONFIDENCE_BUCKETS}
    for row in rows:
        grouped[_bucket(float(row["model_probability"]), CONFIDENCE_BUCKETS)].append(row)
    result = {}
    for label, values in grouped.items():
        if not values:
            continue
        predicted = _safe_mean(v["model_probability"] for v in values)
        actual = _safe_mean(1.0 if v["classification_correct"] else 0.0 for v in values)
        result[label] = {"predictions": len(values), "mean_predicted_probability": predicted,
                         "empirical_success_rate": actual, "calibration_difference": actual-predicted}
    return result


def _timing(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[_bucket(float(row.get("seconds_remaining", -1)), TIMING_BUCKETS)].append(row)
    out = {}
    for label, values in grouped.items():
        stats = _stats(values, n_boot=100)
        stats.update(yes=sum(v.get("side") == "YES" for v in values),
                     no=sum(v.get("side") == "NO" for v in values),
                     mean_confidence=_safe_mean(v.get("model_probability") for v in values),
                     mean_fragility=_safe_mean(v.get("fragility") for v in values))
        out[label] = stats
    return out


def forward_report(store: ForwardStore, n_boot=400):
    observations = store.read("observations"); entries = store.read("entries")
    resolutions = store.read("resolutions"); events = store.read("window_events")
    primary_selections = store.read("primary_selections")
    latest = {}
    for observation in observations: latest[_key(observation)] = observation
    all_keys = set(latest) | {_key(e) for e in events} | {_key(r) for r in resolutions}
    eligible = {key for key, row in latest.items() if row.get("phase6_state") != "DATA HOLD"}
    resolved_entries = _resolved_entries(store); resolved_stats = _stats(resolved_entries, n_boot=n_boot)
    entry_keys = {_key(e) for e in entries}
    status = Counter()
    event_by_key = defaultdict(list)
    for event in events: event_by_key[_key(event)].append(event)
    for key in all_keys:
        if key in entry_keys:
            side = next((e.get("side") for e in entries if _key(e) == key), None)
            status[f"ENTER {side}"] += 1
        elif key in latest:
            decision = latest[key].get("final_decision") or latest[key].get("phase6_state") or "NO TRADE"
            status["DATA HOLD" if decision == "DATA HOLD" else "NO TRADE"] += 1
        else:
            reasons = " ".join(str(e.get("reason", "")) for e in event_by_key[key])
            status["PROVIDER FAILURE" if "UNAVAILABLE" in reasons or "PROVIDER" in reasons else "INSUFFICIENT DATA"] += 1
    report = {
        "policy_identifier": POLICY_IDENTIFIER, "historical": HISTORICAL,
        "total_contract_windows": len(all_keys), "total_contracts_observed": len(all_keys),
        "eligible_windows": len(eligible), "total_entry_events": len(entries),
        "coverage": len(entries)/len(eligible) if eligible else 0.0,
        "abstention_rate": 1-len(entries)/len(eligible) if eligible else 1.0,
        "yes_entries": sum(e.get("side") == "YES" for e in entries),
        "no_entries": sum(e.get("side") == "NO" for e in entries),
        "resolved_entries": len(resolved_entries), "unresolved_entries": len(store.unresolved_entries()),
        "terminal_status": dict(status), "sample_label": "VERY SMALL SAMPLE" if len(resolved_entries) < 30 else "SMALL SAMPLE" if len(resolved_entries) < 100 else "EARLY EVIDENCE" if len(resolved_entries) < 300 else "MEANINGFUL FORWARD SAMPLE",
        "forward_status": resolved_stats.get("sample_status", "SAMPLE TOO SMALL"),
        "classification_accuracy": resolved_stats["accuracy"], "ci_low": resolved_stats["ci_low"],
        "ci_high": resolved_stats["ci_high"], "correct": resolved_stats["correct"],
        "incorrect": resolved_stats["incorrect"], "distinct_windows": resolved_stats["distinct_windows"],
        "primary_selection_policy": "PRIMARY_SELECTOR_V1",
        "primary_selection_count": len(primary_selections),
        "per_asset_qualification_count": len(entries),
    }
    report["yes"] = _stats([r for r in resolved_entries if r.get("side") == "YES"], n_boot=100)
    report["no"] = _stats([r for r in resolved_entries if r.get("side") == "NO"], n_boot=100)
    report["calibration_entry_time"] = _calibration(resolved_entries)
    entry_loss_rows = [{"probability": r["model_probability"], "outcome": 1.0 if r["classification_correct"] else 0.0} for r in resolved_entries]
    report["entry_time_scores"] = _losses(entry_loss_rows)
    resolution_map = {_key(r): r for r in resolutions}
    observation_loss_rows = []
    for observation in observations:
        resolution = resolution_map.get(_key(observation))
        if resolution and resolution.get("winning_side") in ("YES", "NO"):
            observation_loss_rows.append({"probability": observation.get("p_yes"),
                                          "outcome": 1.0 if resolution["winning_side"] == "YES" else 0.0})
    report["all_eligible_observation_scores"] = _losses(observation_loss_rows)
    observation_calibration_rows = []
    for observation in observations:
        resolution = resolution_map.get(_key(observation))
        if not resolution or resolution.get("winning_side") not in ("YES", "NO"):
            continue
        confidence = max(float(observation.get("p_yes", .5)), float(observation.get("p_no", .5)))
        predicted = observation.get("predicted_side")
        observation_calibration_rows.append({"model_probability": confidence,
            "classification_correct": predicted == resolution["winning_side"]})
    report["calibration_all_eligible_observations"] = _calibration(observation_calibration_rows)
    report["entry_timing"] = _timing(resolved_entries)
    assets = sorted({key[1] for key in all_keys if key[1]})
    per_asset = {}
    for asset in assets:
        asset_keys = {k for k in all_keys if k[1] == asset}; asset_entries = [e for e in entries if e.get("asset") == asset]
        asset_resolved = [r for r in resolved_entries if r.get("asset") == asset]
        stats = _stats(asset_resolved, n_boot=100)
        stats.update(eligible_windows=sum(k in eligible for k in asset_keys), entries=len(asset_entries),
                     coverage=len(asset_entries)/sum(k in eligible for k in asset_keys) if sum(k in eligible for k in asset_keys) else 0.0,
                     yes_accuracy=_stats([r for r in asset_resolved if r.get("side") == "YES"], n_boot=50)["accuracy"],
                     no_accuracy=_stats([r for r in asset_resolved if r.get("side") == "NO"], n_boot=50)["accuracy"],
                     mean_entry_seconds=_safe_mean(e.get("seconds_remaining") for e in asset_entries),
                     mean_confidence=_safe_mean(e.get("model_probability") for e in asset_entries),
                     data_holds=sum(e.get("status") == "DATA HOLD" for e in events if e.get("asset") == asset),
                     proxy_references=sum(r.get("reference_status") != "OFFICIAL_VERIFIED_REFERENCE" for r in asset_resolved))
        per_asset[asset] = stats
    report["per_asset"] = per_asset
    report["provider_failure_count"] = status["PROVIDER FAILURE"]
    report["data_hold_count"] = status["DATA HOLD"]
    report["no_trade_count"] = status["NO TRADE"]
    report["milestones"] = {str(m): "REACHED" if len(entries) >= m else "PENDING" for m in MILESTONES}
    report["reference_provenance"] = dict(Counter(
        r.get("reference_status", "UNKNOWN") for r in resolved_entries))
    report["provider_health_breakdown"] = dict(Counter(
        r.get("provider_health", "UNKNOWN") for r in resolved_entries))
    report["volatility_regimes"] = dict(Counter(
        r.get("volatility_regime", "UNKNOWN") for r in resolved_entries))
    report["drift"] = drift_status(report)
    health = store.read("provider_health")
    report["provider_reliability"] = {"health_records": len(health),
        "successful_fetches": max([int(x.get("successful_fetches", 0)) for x in health], default=0),
        "failed_fetches": max([int(x.get("failed_fetches", 0)) for x in health], default=0),
        "stale_observations": sum(float(x.get("data_age_seconds", 0)) > 90 for x in observations),
        "data_holds": report["data_hold_count"]}
    return report


def drift_status(report):
    n = report.get("resolved_entries", 0)
    if n < 50: return {"status": "INSUFFICIENT SAMPLE", "minimum_for_watch": 50, "minimum_for_material": 100}
    accuracy = report.get("classification_accuracy"); coverage = report.get("coverage", 0)
    material = n >= 100 and ((report.get("ci_high") is not None and report["ci_high"] < HISTORICAL["ci_low"]) or abs(coverage-HISTORICAL["coverage"]) > .20)
    watch = abs((accuracy or HISTORICAL["accuracy"])-HISTORICAL["accuracy"]) > .04 or abs(coverage-HISTORICAL["coverage"]) > .12
    return {"status": "MATERIAL DEVIATION" if material else "WATCH" if watch else "WITHIN EXPECTED RANGE",
            "historical_context": HISTORICAL}


def daily_report(store, date=None, n_boot=200):
    date = date or datetime.now(UTC).date().isoformat()
    subset = _FilteredStore(store, date)
    report = forward_report(subset, n_boot=n_boot); report["date_utc"] = date
    return report


class _FilteredStore:
    def __init__(self, store, date): self.store, self.date = store, date
    def read(self, name):
        field = {"observations": "timestamp_utc", "entries": "entry_timestamp", "resolutions": "resolution_timestamp",
                 "window_events": "timestamp_utc", "provider_health": "timestamp_utc", "runs": "timestamp_utc",
                 "session_events": "timestamp_utc"}.get(name, "timestamp_utc")
        return [r for r in self.store.read(name) if str(r.get(field, "")).startswith(self.date)]
    def unresolved_entries(self):
        resolutions = {_key(r) for r in self.read("resolutions")}
        return [e for e in self.read("entries") if _key(e) not in resolutions]


def manifest(store: ForwardStore):
    rows = {name: store.read(name) for name in store.FILES}
    stamps = []
    for values in rows.values():
        for row in values:
            for field in ("timestamp_utc", "entry_timestamp", "resolution_timestamp"):
                if row.get(field): stamps.append(row[field]); break
    files = {}
    for name, filename in store.FILES.items():
        path = store.directory / filename
        files[name] = {"file": filename, "rows": len(rows[name]),
                       "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None,
                       "schema_version": store.SCHEMA_VERSIONS[name]}
    contracts = {_key(o) for o in rows["observations"]} | {_key(e) for e in rows["window_events"]}
    policies = sorted({str(x.get("policy_identifier")) for name in ("runs", "entries") for x in rows[name] if x.get("policy_identifier")})
    selection_policies=sorted({str(x.get("selection_policy")) for x in rows["primary_selections"] if x.get("selection_policy")})
    return {"first_forward_timestamp": min(stamps) if stamps else None, "latest_forward_timestamp": max(stamps) if stamps else None,
            "files": files, "run_count": len(rows["runs"]), "observation_count": len(rows["observations"]),
            "contract_count": len(contracts), "entry_count": len(rows["entries"]),
            "resolution_count": len(rows["resolutions"]), "unresolved_count": len(store.unresolved_entries()),
            "policy_versions": policies, "selection_policy_versions": selection_policies}


def audit_contract(store, contract_id):
    observations = [r for r in store.read("observations") if r.get("contract_id") == contract_id]
    entries = [r for r in store.read("entries") if r.get("contract_id") == contract_id]
    resolutions = [r for r in store.read("resolutions") if r.get("contract_id") == contract_id]
    events = [r for r in store.read("window_events") if r.get("contract_id") == contract_id]
    return {"contract_id": contract_id, "observations": sorted(observations, key=lambda r:r.get("timestamp_utc", "")),
            "entries": entries, "resolutions": resolutions, "window_events": events, "read_only": True}


def incorrect_signals(store):
    output = [{k: row.get(k) for k in ("asset", "contract_id", "side", "model_probability", "lower_bound",
             "fragility", "disagreement", "crossing_probability", "seconds_remaining", "entry_underlying_price",
             "reference", "reference_status", "provider_health", "winning_side", "terminal_value", "resolution_source")}
            for row in _resolved_entries(store) if row.get("classification_correct") is False]
    for row in output:
        if isinstance(row.get("entry_underlying_price"), (int, float)) and isinstance(row.get("reference"), (int, float)):
            row["entry_reference_gap"] = row["entry_underlying_price"] - row["reference"]
    return output


def render_report(report, title="MANTIS PHASE 11 FORWARD REPORT"):
    def pct(value): return "N/A" if value is None else f"{100*value:.1f}%"
    lines = [title, "CLASSIFICATION RESEARCH ONLY — NOT A PROFITABILITY BACKTEST", "",
             f"POLICY ................ {report.get('policy_identifier', POLICY_IDENTIFIER)}",
             f"TOTAL CONTRACT WINDOWS  {report.get('total_contract_windows', 0)}",
             f"ELIGIBLE WINDOWS ....... {report.get('eligible_windows', 0)}",
             f"ENTRIES / COVERAGE ..... {report.get('total_entry_events', 0)} / {pct(report.get('coverage'))}",
             f"YES / NO ............... {report.get('yes_entries', 0)} / {report.get('no_entries', 0)}",
             f"RESOLVED / UNRESOLVED .. {report.get('resolved_entries', 0)} / {report.get('unresolved_entries', 0)}",
             f"CORRECT / INCORRECT .... {report.get('correct', 0)} / {report.get('incorrect', 0)}",
             f"ACCURACY ............... {pct(report.get('classification_accuracy'))}",
             f"CLUSTERED 95% CI ....... {pct(report.get('ci_low'))} — {pct(report.get('ci_high'))}",
             f"SAMPLE STATUS .......... {report.get('sample_label', 'SAMPLE TOO SMALL')}",
             f"DRIFT STATUS ........... {report.get('drift', {}).get('status', 'INSUFFICIENT SAMPLE')}",
             f"NO TRADE / DATA HOLD ... {report.get('no_trade_count', 0)} / {report.get('data_hold_count', 0)}",
             f"PROVIDER FAILURES ...... {report.get('provider_failure_count', 0)}"]
    if report.get("per_asset"):
        lines.extend(["", "BY ASSET"])
        for asset, value in report["per_asset"].items():
            lines.append(f"{asset:<10} entries {value['entries']:>4}  coverage {pct(value['coverage']):>6}  accuracy {pct(value['accuracy']):>6}")
    scores = report.get("entry_time_scores", {})
    lines.extend(["", f"ENTRY BRIER / LOG LOSS . {scores.get('brier_score', 'N/A')} / {scores.get('log_loss', 'N/A')}",
                  "FORWARD ACCURACY DOES NOT ESTABLISH PROFITABILITY"])
    return "\n".join(lines)


def render_manifest(value): return json.dumps(value, indent=2, sort_keys=True)
