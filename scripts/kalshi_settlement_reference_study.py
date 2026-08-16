"""Step 7 settlement/reference fidelity study; never imports the live stack."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from datetime import UTC, datetime
from decimal import Decimal

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from mantis_v4.models.kalshi_step5 import chronological_group_split
from mantis_v4.reference.cf_benchmarks import BENCHMARK_BY_ASSET, DIRECT_CF_DATA_STATUS
from mantis_v4.reference.settlement import (ENDING_RECONSTRUCTION_VERSION,
    REFERENCE_VALIDATION_VERSION, SETTLEMENT_SPEC_VERSION, STATUS,
    classify_bounded_sensitivity, event_specifications, paired_error_metrics,
    side_flip, specification_manifest)
from scripts.kalshi_complementarity_study import build_dataset
from scripts.kalshi_ml_study import entry_mask, file_set_hash


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def metrics_by_asset(frame, proxy, official, target):
    return {asset: paired_error_metrics(block, proxy_col=proxy, official_col=official, target_col=target)
            for asset, block in frame.groupby("asset", sort=True)}


def grouped_flip(frame, group_col, proxy, official, target):
    return {str(key): {"n": int(len(block)), "side_flip_rate": float(side_flip(
        block[proxy], block[official], block[target]).mean())}
        for key, block in frame.groupby(group_col, observed=True, sort=True)}


def grouped_error(frame, group_col, proxy, official, target):
    return {str(key): paired_error_metrics(block, proxy_col=proxy, official_col=official, target_col=target)
            for key, block in frame.groupby(group_col, observed=True, sort=True) if len(block) >= 2}


def main():
    market_path, assets, frames, joined, states, _ = build_dataset()
    markets = pd.read_csv(market_path)
    markets["window_start_utc"] = pd.to_datetime(markets.window_start_utc, utc=True)
    markets["window_end_utc"] = pd.to_datetime(markets.window_end_utc, utc=True)
    markets["ending_benchmark"] = pd.to_numeric(markets.ending_benchmark, errors="coerce")
    missing_official_rows = markets[markets.ending_benchmark.isna()][["asset", "market_ticker"]].to_dict("records")

    # One causal proxy reference and one outcome-time terminal proxy per asset-contract.
    contract = joined.sort_values("scan_utc").groupby(["contract_id", "asset"], as_index=False).agg(
        window_start_utc=("window_start_utc", "first"), window_end_utc=("window_end_utc", "first"),
        yahoo_start_proxy=("old_proxy_reference", "first"), yahoo_terminal_proxy=("terminal_price", "first"),
        kalshi_target=("kalshi_target", "first"), realized_vol_1m=("realized_vol_1m", "first"))
    contract = contract.merge(markets[["asset", "window_start_utc", "window_end_utc", "ending_benchmark", "outcome_yes"]],
                              on=["asset", "window_start_utc", "window_end_utc"], how="inner",
                              validate="one_to_one")
    contract["hour_utc"] = pd.to_datetime(contract.window_start_utc, utc=True).dt.hour
    contract["weekend"] = np.where(pd.to_datetime(contract.window_start_utc, utc=True).dt.dayofweek >= 5,
                                    "WEEKEND", "WEEKDAY")
    contract["start_distance_bps"] = 10000 * (contract.yahoo_start_proxy-contract.kalshi_target)/contract.kalshi_target
    contract["distance_band"] = pd.cut(contract.start_distance_bps.abs(), [-np.inf, 1, 2, 5, np.inf],
                                       labels=["EXTREMELY_CLOSE_LT_1BP", "CLOSE_1_2BP", "MODERATE_2_5BP", "FAR_GE_5BP"])
    # Volatility thresholds are fixed from the Step 5 development windows only.
    splits = chronological_group_split(joined.group_key, joined.window_end_utc)
    dev_windows = set(joined.loc[splits["development"], "group_key"])
    dev_contract = contract[contract.contract_id.isin(dev_windows)]
    v0, v1 = [float(dev_contract.realized_vol_1m.quantile(q)) for q in (.333333, .666667)]
    contract["volatility_regime"] = pd.cut(contract.realized_vol_1m, [-np.inf, v0, v1, np.inf],
                                           labels=["LOW", "MEDIUM", "HIGH"])
    paired_ending = contract.dropna(subset=["ending_benchmark"]).copy()

    start_metrics = metrics_by_asset(contract, "yahoo_start_proxy", "kalshi_target", "kalshi_target")
    ending_metrics = metrics_by_asset(paired_ending, "yahoo_terminal_proxy", "ending_benchmark", "kalshi_target")
    official_reconstructed = paired_ending.ending_benchmark >= paired_ending.kalshi_target
    reproduction_match = official_reconstructed.to_numpy(int) == paired_ending.outcome_yes.to_numpy(int)

    holdout = splits["sealed_holdout"]
    holdout_entry_mask, entries = entry_mask(states, holdout)
    if int(holdout_entry_mask.sum()) != 1377 or len(entries) != 2111 or not np.isclose(entries.entered.mean(), 1377/2111):
        raise RuntimeError("frozen Step 4 holdout cohort reproduction failed")
    entry_rows = joined.loc[holdout_entry_mask, ["asset", "spot", "kalshi_target"]].copy()
    sensitivity = {}
    for bound in (1, 2, 5, 10):
        labels = [classify_bounded_sensitivity(value=Decimal(str(v)), target=Decimal(str(t)),
                                                error_bound_bps=Decimal(bound))
                  for v, t in zip(entry_rows.spot, entry_rows.kalshi_target)]
        entry_rows[f"sensitivity_{bound}bp"] = labels
        sensitivity[f"PLUS_MINUS_{bound}BP"] = {
            "overall": pd.Series(labels).value_counts().to_dict(),
            "by_asset": {asset: block[f"sensitivity_{bound}bp"].value_counts().to_dict()
                         for asset, block in entry_rows.groupby("asset")}}

    # P95 starting-reference gaps can inform a labeled sensitivity exercise, not actual truth.
    empirical_p95_sensitivity = {}
    for asset, block in entry_rows.groupby("asset"):
        bound = Decimal(str(start_metrics[asset]["p95_absolute_bps"]))
        labels = [classify_bounded_sensitivity(value=Decimal(str(v)), target=Decimal(str(t)),
                                                error_bound_bps=bound)
                  for v, t in zip(block.spot, block.kalshi_target)]
        empirical_p95_sensitivity[asset] = {"bound_bps": float(bound), **pd.Series(labels).value_counts().to_dict()}

    retrieved = datetime.now(UTC)
    specs = event_specifications(retrieved)
    history_paths = [p for asset in assets for p in (ROOT / "data/history").glob(f"{asset}_1m_*.csv")]
    provenance = {
        "version": "STEP7_DATA_PROVENANCE_V1", "retrieval_timestamp_utc": retrieved.isoformat(),
        "datasets": [
            {"provider": "KALSHI_PUBLIC_REST", "product": "finalized KX crypto 15m markets",
             "assets": assets, "endpoint": "/markets + /historical/markets", "time_range": [
                 markets.window_start_utc.min().isoformat(), markets.window_end_utc.max().isoformat()],
             "frequency": "15-minute contracts", "authorization": "ANONYMOUS_PUBLIC",
             "checksum_sha256": sha256(market_path), "row_count": int(len(markets)),
             "missing_data_count": int(markets.ending_benchmark.isna().sum()),
             "missing_official_aggregate_rows": missing_official_rows,
             "timestamp_semantics": "open_time/close_time UTC; expiration_value is official finalized aggregate",
             "transformation": "strict finalized greater_or_equal parser"},
            {"provider": "YAHOO_FINANCE", "product": "1-minute OHLC proxy", "assets": assets,
             "endpoint": "cached yfinance history files", "frequency": "1 minute",
             "authorization": "PUBLIC_PROXY_CACHE", "row_count": int(sum(len(x) for x in frames.values())),
             "checksum_sha256": file_set_hash(history_paths),
             "time_range": [min(frame.index.min() for frame in frames.values()).isoformat(),
                            max(frame.index.max() for frame in frames.values()).isoformat()],
             "missing_data_count": 0, "timestamp_semantics": "bar-labelled UTC; close visible at/after bar",
             "transformation": "causal Step 4 dataset builder; never labelled CF Benchmarks"},
            {"provider": "CF_BENCHMARKS", "product": "BRTI/ETHUSD_RTI/SOLUSD_RTI/XRPUSD_RTI",
             "assets": assets, "endpoint": "documented values/index directory checked anonymously",
             "frequency": "approximately 1 second", "authorization": "LICENSE_REQUIRED_NOT_CONFIGURED",
             "row_count": 0, "missing_data_count": None,
             "timestamp_semantics": "direct observation sequence unavailable",
             "access_observation": "/api/v1/indices returned an empty payload; values requests returned HTTP 400 without licensed access",
             "transformation": "NONE; NO SILENT YAHOO FALLBACK"}],
    }
    report = {
        "status": STATUS, "versions": {"settlement": SETTLEMENT_SPEC_VERSION,
            "reference": REFERENCE_VALIDATION_VERSION, "ending": ENDING_RECONSTRUCTION_VERSION},
        "event_specification": specification_manifest(specs),
        "direct_cf_data_status": DIRECT_CF_DATA_STATUS,
        "cf_access_by_asset": {asset: {"status": "LICENSE_REQUIRED", "benchmark": BENCHMARK_BY_ASSET[asset]}
                               for asset in assets},
        "provenance": provenance,
        "starting_proxy_vs_official_kalshi_target": {
            "interpretation": "MEASURED: Yahoo proxy window reference versus official Kalshi starting target; not synchronized instantaneous CF",
            "per_asset": start_metrics,
            "error_by_volatility": grouped_error(contract, "volatility_regime", "yahoo_start_proxy", "kalshi_target", "kalshi_target"),
            "error_by_hour_utc": grouped_error(contract, "hour_utc", "yahoo_start_proxy", "kalshi_target", "kalshi_target"),
            "error_by_weekend": grouped_error(contract, "weekend", "yahoo_start_proxy", "kalshi_target", "kalshi_target"),
            "error_by_target_distance": grouped_error(contract, "distance_band", "yahoo_start_proxy", "kalshi_target", "kalshi_target"),
            "flip_by_volatility": grouped_flip(contract, "volatility_regime", "yahoo_start_proxy", "kalshi_target", "kalshi_target"),
            "flip_by_hour_utc": grouped_flip(contract, "hour_utc", "yahoo_start_proxy", "kalshi_target", "kalshi_target"),
            "flip_by_weekend": grouped_flip(contract, "weekend", "yahoo_start_proxy", "kalshi_target", "kalshi_target")},
        "terminal_proxy_vs_official_ending_average": {
            "interpretation": "MEASURED COMBINED DIFFERENCE: Yahoo terminal proxy versus official Kalshi expiration_value; cannot isolate CF terminal-versus-average without licensed 1Hz observations",
            "per_asset": ending_metrics,
            "error_by_volatility": grouped_error(paired_ending, "volatility_regime", "yahoo_terminal_proxy", "ending_benchmark", "kalshi_target"),
            "error_by_hour_utc": grouped_error(paired_ending, "hour_utc", "yahoo_terminal_proxy", "ending_benchmark", "kalshi_target"),
            "error_by_weekend": grouped_error(paired_ending, "weekend", "yahoo_terminal_proxy", "ending_benchmark", "kalshi_target"),
            "flip_by_distance": grouped_flip(paired_ending, "distance_band", "yahoo_terminal_proxy", "ending_benchmark", "kalshi_target"),
            "flip_by_volatility": grouped_flip(paired_ending, "volatility_regime", "yahoo_terminal_proxy", "ending_benchmark", "kalshi_target"),
            "flip_by_hour_utc": grouped_flip(paired_ending, "hour_utc", "yahoo_terminal_proxy", "ending_benchmark", "kalshi_target"),
            "flip_by_weekend": grouped_flip(paired_ending, "weekend", "yahoo_terminal_proxy", "ending_benchmark", "kalshi_target")},
        "settlement_reproduction_from_official_aggregate": {"n": int(len(paired_ending)),
            "unavailable": int(len(contract)-len(paired_ending)),
            "matches": int(reproduction_match.sum()), "mismatches": int((~reproduction_match).sum()),
            "match_rate": float(reproduction_match.mean()),
            "qualification": "Uses official expiration_value, not independently reconstructed 1Hz observations"},
        "exact_ending_60s_reconstruction": {"status": "UNAVAILABLE_NO_LICENSED_1HZ_CF_OBSERVATIONS",
            "windows": 0, "terminal_vs_same_source_average": "UNRESOLVED"},
        "qualified_entry_robustness": {"actual_direct_cf_classification": {
                "REFERENCE_ROBUST": 0, "REFERENCE_AMBIGUOUS": 0, "REFERENCE_FLIPPED": 0,
                "REFERENCE_UNKNOWN": 1377},
            "fixed_holdout": {"entries": 1377, "eligible": 2111, "coverage": 1377/2111,
                              "label": "PREVIOUSLY_OPENED_HOLDOUT_NOT_NEW_SEALED"},
            "fixed_perturbation_sensitivity_not_actual_error": sensitivity,
            "empirical_start_gap_p95_sensitivity_not_actual_current_cf_error": empirical_p95_sensitivity},
        "new_step7_forward_sample": "INSUFFICIENT_DATA",
        "new_rows_after_step5_holdout": 0,
        "readiness_decision": "SETTLEMENT_REFERENCE_PARTIALLY_VALIDATED",
        "prohibitions": {"gates_modified": False, "step5_model_modified": False,
                         "ensemble_created": False, "live_selector_integration": False,
                         "execution_integration": False, "position_management": False,
                         "early_exit_logic": False},
    }
    out = ROOT / "data" / "kalshi_step7_summary.json"
    out.write_text(json.dumps(report, indent=2, allow_nan=False, default=str), encoding="utf-8")
    (ROOT / "data/kalshi_step7_provenance.json").write_text(
        json.dumps(provenance, indent=2, allow_nan=False, default=str), encoding="utf-8")
    print(json.dumps({"status": report["status"], "contracts": len(contract),
                      "direct_cf_data_status": DIRECT_CF_DATA_STATUS,
                      "official_aggregate_settlement_match_rate": reproduction_match.mean(),
                      "qualified_entries": 1377, "new_step7_forward_sample": report["new_step7_forward_sample"],
                      "decision": report["readiness_decision"], "output": str(out)}, indent=2))


if __name__ == "__main__":
    main()
