"""Step 8 transparent Kalshi reference-risk shadow study."""
from __future__ import annotations

import json
from pathlib import Path
import sys
from decimal import Decimal

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from mantis_v4.models.kalshi_step5 import chronological_group_split
from mantis_v4.reference.kalshi_reference_risk import (CURRENT_VALUE_PROVENANCE,
    EMPIRICAL_QUANTILES, EVENT_REFERENCE_PROVENANCE, FIXED_BOUNDS_BPS,
    OPENED_HOLDOUT_LABEL, POLICY_VERSION, STATUS, evaluate_shadow_cohort,
    fit_empirical_bounds, near_target_bucket, time_remaining_bucket)
from mantis_v4.reference.settlement import paired_error_metrics
from scripts.kalshi_complementarity_study import build_dataset
from scripts.kalshi_ml_study import entry_mask


def grouped_outcomes(frame, column):
    result = {}
    for key, block in frame.groupby(column, observed=True, sort=True):
        pred = block.p_yes.to_numpy(float) >= .5; y = block.outcome_yes.to_numpy(int).astype(bool)
        result[str(key)] = {"n": int(len(block)), "accuracy": float(np.mean(pred == y)),
                            "mean_abs_distance_bps": float(block.abs_distance_bps.mean()),
                            "mean_fragility": float(block.fragility.mean()),
                            "mean_crossing_probability": float(block.crossing_risk.mean())}
    return result


def combined_endpoint_by_entry_time(entry_rows, contract):
    merged = entry_rows[["contract_id", "asset", "seconds_remaining"]].merge(
        contract, on=["contract_id", "asset"], how="inner", validate="one_to_one")
    merged = merged.dropna(subset=["ending_benchmark"])
    merged["entry_time_bucket"] = merged.seconds_remaining.map(time_remaining_bucket)
    return {bucket: paired_error_metrics(block, proxy_col="yahoo_terminal_proxy",
                                          official_col="ending_benchmark", target_col="kalshi_target")
            for bucket, block in merged.groupby("entry_time_bucket", sort=True) if len(block) >= 2}


def main():
    market_path, assets, frames, joined, states, _ = build_dataset()
    splits = chronological_group_split(joined.group_key, joined.window_end_utc)
    state = joined.copy()
    for column in ("p_yes", "fragility", "disagreement", "crossing_risk"):
        state[column] = states[column].to_numpy()
    state["distance_bps"] = 10000 * (state.spot-state.kalshi_target)/state.kalshi_target
    state["abs_distance_bps"] = state.distance_bps.abs()
    state["near_target_bucket"] = state.abs_distance_bps.map(near_target_bucket)
    state["time_remaining_bucket"] = state.seconds_remaining.map(time_remaining_bucket)

    contract = state.sort_values("scan_utc").groupby(["contract_id", "asset"], as_index=False).agg(
        window_end_utc=("window_end_utc", "first"), yahoo_start_proxy=("old_proxy_reference", "first"),
        yahoo_terminal_proxy=("terminal_price", "first"), kalshi_target=("kalshi_target", "first"),
        realized_vol_1m=("realized_vol_1m", "first"))
    markets = pd.read_csv(market_path)
    markets["window_end_utc"] = pd.to_datetime(markets.window_end_utc, utc=True)
    contract = contract.merge(markets[["asset", "window_end_utc", "ending_benchmark"]],
                              on=["asset", "window_end_utc"], how="left", validate="one_to_one")
    dev_groups = set(state.loc[splits["development"], "group_key"])
    empirical = fit_empirical_bounds(contract[contract.contract_id.isin(dev_groups)])
    v0, v1 = [float(state.loc[splits["development"], "realized_vol_1m"].quantile(q)) for q in (.333333, .666667)]
    state["volatility_regime"] = pd.cut(state.realized_vol_1m, [-np.inf, v0, v1, np.inf],
                                        labels=["LOW", "MEDIUM", "HIGH"])
    state["fragility_band"] = pd.cut(state.fragility, [-np.inf, 10, 25, 50, np.inf],
                                     labels=["LE_10", "10_25", "25_50", "GT_50"])
    state["crossing_probability_band"] = pd.cut(state.crossing_risk, [-np.inf, .10, .20, .35, np.inf],
                                                 labels=["LE_010", "010_020", "020_035", "GT_035"])

    results = {}
    entry_frames = {}
    # Contract identity is disjoint across chronological splits, so applying the
    # frozen one-entry policy once is equivalent and avoids three costly passes.
    full_entry_mask, _ = entry_mask(states, np.ones(len(states), dtype=bool))
    for split_name, split_mask in splits.items():
        entry_row_mask = full_entry_mask & split_mask
        eligible_contracts = int(states.loc[split_mask].groupby(["contract_id", "asset"]).ngroups)
        cohort = state.loc[entry_row_mask].copy(); entry_frames[split_name] = cohort
        if split_name == "sealed_holdout" and (len(cohort) != 1377 or eligible_contracts != 2111):
            raise RuntimeError("fixed Step 4 holdout cohort changed")
        bounds = {f"FIXED_{bound}BP": {asset: bound for asset in assets} for bound in FIXED_BOUNDS_BPS}
        for quantile in EMPIRICAL_QUANTILES:
            bounds[f"DEVELOPMENT_EMPIRICAL_{quantile}"] = {asset: empirical[asset][quantile] for asset in assets}
        evaluations = {name: evaluate_shadow_cohort(cohort, per_asset, eligible_contracts=eligible_contracts)
                       for name, per_asset in bounds.items()}
        results[split_name] = {
            "label": OPENED_HOLDOUT_LABEL if split_name == "sealed_holdout" else split_name.upper(),
            "rows": int(split_mask.sum()), "qualified_entries": int(len(cohort)),
            "eligible_contracts": eligible_contracts, "bounds": evaluations,
            "near_target": grouped_outcomes(cohort, "near_target_bucket"),
            "time_remaining": grouped_outcomes(cohort, "time_remaining_bucket"),
            "volatility": grouped_outcomes(cohort, "volatility_regime"),
            "crossing_probability": grouped_outcomes(cohort, "crossing_probability_band"),
            "fragility": grouped_outcomes(cohort, "fragility_band")}

    holdout_entries = entry_frames["sealed_holdout"]
    step7 = json.loads((ROOT/"data/kalshi_step7_summary.json").read_text(encoding="utf-8"))
    report = {
        "policy_version": POLICY_VERSION, "status": STATUS,
        "methodology": "Symmetric explicit uncertainty bands around actual Kalshi target; ROBUST iff both perturbation endpoints retain the proxy-implied side",
        "event_reference_provenance": EVENT_REFERENCE_PROVENANCE,
        "current_value_provenance": CURRENT_VALUE_PROVENANCE,
        "bounds_fitted_on_development_only": {asset: {q: float(v) for q, v in values.items()}
                                               for asset, values in empirical.items()},
        "volatility_thresholds_fitted_on_development_only": [v0, v1],
        "splits": results,
        "combined_source_plus_averaging_error": {
            "label": "COMBINED_SOURCE_PLUS_AVERAGING_ERROR",
            "per_asset": step7["terminal_proxy_vs_official_ending_average"]["per_asset"],
            "by_distance": step7["terminal_proxy_vs_official_ending_average"]["flip_by_distance"],
            "by_volatility": step7["terminal_proxy_vs_official_ending_average"]["flip_by_volatility"],
            "by_entry_time_opened_holdout": combined_endpoint_by_entry_time(holdout_entries, contract),
            "does_not_isolate_60s_averaging": True},
        "new_forward_sample": "NEW_FORWARD_SAMPLE_INSUFFICIENT",
        "new_rows_strictly_after_step5_holdout": 0,
        "production_authorization": False,
        "prohibitions": {"normal_z_modified": False, "gates_modified": False,
                         "step5_model_retrained": False, "ensemble_created": False,
                         "live_selector_connected": False, "voice_connected": False,
                         "ui_actionability_connected": False, "economics_connected": False,
                         "execution_connected": False, "forward_selection_records_connected": False},
    }
    out = ROOT/"data/kalshi_step8_reference_risk_summary.json"
    out.write_text(json.dumps(report, indent=2, allow_nan=False, default=str), encoding="utf-8")
    h = results["sealed_holdout"]["bounds"]
    print(json.dumps({"policy": POLICY_VERSION, "status": STATUS,
                      "opened_holdout_entries": 1377,
                      "fixed_5bp": h["FIXED_5BP"],
                      "empirical_p95": h["DEVELOPMENT_EMPIRICAL_P95"],
                      "new_forward_sample": report["new_forward_sample"],
                      "production_authorization": False, "output": str(out)}, indent=2))


if __name__ == "__main__":
    main()
