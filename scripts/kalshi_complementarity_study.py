"""Run Step 6 frozen statistical-vs-ML complementarity research."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from mantis_v4.backtest.history import InMemoryHistoryProvider, load_universe
from mantis_v4.clock import load_timezone
from mantis_v4.config import MantisConfig
from mantis_v4.economics.kalshi import SERIES_BY_ASSET
from mantis_v4.economics.kalshi_reference import TRANSFERRED_POLICY, build_kalshi_states
from mantis_v4.models.dataset_builder import ModellingDatasetBuilder
from mantis_v4.models.kalshi_step5 import (SEED, build_causal_features,
    chronological_group_split, load_model, schema_hash)
from mantis_v4.models.kalshi_step6 import (MODEL_COMPLEMENTARITY_POLICY,
    OPENED_HOLDOUT_LABEL, STATUS, analyze_split, apply_regimes,
    build_comparison_states, fit_development_regimes, incremental_information,
    sha256_file, verify_frozen_artifacts)
from scripts.kalshi_ml_study import entry_mask, file_set_hash, split_description
from scripts.kalshi_reference_study import cached_frames, contracts_from_csv, rows_for, target_crossings


def step5_config_hash() -> str:
    config = {"seed": SEED, "models": ["statistical", "logistic", "hist_gradient_boosting"],
              "calibrations": ["identity", "platt", "isotonic"],
              "selection_metric": ["validation_all_state_brier", "validation_all_state_log_loss"],
              "transferred_gates": TRANSFERRED_POLICY.__dict__, "live_actionability": False}
    return hashlib.sha256(json.dumps(config, sort_keys=True, default=str).encode()).hexdigest()


def build_dataset():
    market_path = ROOT / "data" / "kalshi_reference_v1_markets.csv"
    contracts = contracts_from_csv(market_path)
    assets = list(SERIES_BY_ASSET)
    frames = cached_frames(ROOT / "data" / "history", assets)
    if set(frames) != set(assets):
        raise RuntimeError("cached causal history incomplete")
    loaded, _ = load_universe(InMemoryHistoryProvider(frames), assets, 10_000)
    table, _ = ModellingDatasetBuilder(loaded, load_timezone(MantisConfig.load().contract_timezone)).build(assets)
    market = pd.DataFrame(rows_for(contracts))
    market["window_start_utc"] = pd.to_datetime(market.window_start_utc, utc=True)
    market["window_end_utc"] = pd.to_datetime(market.window_end_utc, utc=True)
    joined = table.merge(market[["asset", "window_start_utc", "window_end_utc", "target", "outcome_yes"]],
                         on=["asset", "window_start_utc", "window_end_utc"], how="inner",
                         suffixes=("_proxy", "_kalshi"))
    joined["old_proxy_reference"] = joined.reference
    joined["kalshi_target"] = joined.target.astype(float)
    joined["outcome_yes"] = joined.outcome_yes_kalshi.astype(int)
    joined["crossings"] = target_crossings(joined, frames)
    joined["reference_gap_bps"] = ((joined.kalshi_target - joined.old_proxy_reference) /
                                    joined.old_proxy_reference * 10000)
    states = build_kalshi_states(joined)
    features = build_causal_features(joined, states)
    return market_path, assets, frames, joined, states, features


def main():
    market_path, assets, frames, joined, states, features = build_dataset()
    history_paths = [p for asset in assets for p in (ROOT / "data" / "history").glob(f"{asset}_1m_*.csv")]
    data_hash = file_set_hash([market_path, *history_paths])
    manifest_path = ROOT / "data" / "kalshi_step5_freeze.json"
    model_path = ROOT / "data" / "models" / "kalshi_step5_frozen.joblib"
    manifest = verify_frozen_artifacts(manifest_path, model_path, schema_hash=schema_hash(),
                                       data_hash=data_hash, config_hash=step5_config_hash())
    frozen = load_model(model_path)
    p_ml_a = frozen.predict(features, states.p_yes.to_numpy(float))
    p_ml_b = frozen.predict(features, states.p_yes.to_numpy(float))
    if not np.array_equal(p_ml_a, p_ml_b):
        raise RuntimeError("frozen Step 5 probability reproduction is not deterministic")
    splits = chronological_group_split(joined.group_key, joined.window_end_utc)
    for name, expected in manifest["splits"].items():
        if split_description(splits[name], joined) != expected:
            raise RuntimeError(f"frozen split mismatch: {name}")
    entry_masks = {}
    entry_summaries = {}
    for name, mask in splits.items():
        entry_masks[name], entry_summaries[name] = entry_mask(states, mask)
    all_entry = np.logical_or.reduce(list(entry_masks.values()))
    comparison = build_comparison_states(joined, states, p_ml_a, all_entry)
    thresholds = fit_development_regimes(comparison.loc[splits["development"]])
    comparison = apply_regimes(comparison, thresholds)

    report_splits = {}
    for name, mask in splits.items():
        cohort = comparison.loc[mask]
        report_splits[name] = {
            "all_causal_states": analyze_split(cohort),
            "fixed_step4_qualified_entry_cohort": analyze_split(cohort[cohort.fixed_step4_entry]),
            "rows": int(mask.sum()), "contracts": int(cohort[["contract_id", "asset"]].drop_duplicates().shape[0]),
            "windows": int(cohort.group_key.nunique()),
            "start_utc": pd.to_datetime(cohort.window_end_utc, utc=True).min().isoformat(),
            "end_utc": pd.to_datetime(cohort.window_end_utc, utc=True).max().isoformat(),
            "fixed_entry_count": int(cohort.fixed_step4_entry.sum()),
            "eligible_contracts": int(len(entry_summaries[name])),
            "fixed_entry_coverage": float(entry_summaries[name].entered.mean()),
        }
    holdout = report_splits["sealed_holdout"]
    if holdout["fixed_entry_count"] != 1377 or holdout["eligible_contracts"] != 2111:
        raise RuntimeError("fixed Step 4 holdout cohort failed exact 1377/2111 reproduction")
    expected_coverage = 1377 / 2111
    if not np.isclose(holdout["fixed_entry_coverage"], expected_coverage):
        raise RuntimeError("fixed Step 4 holdout coverage changed")

    last_holdout = pd.Timestamp(manifest["splits"]["sealed_holdout"]["end_utc"])
    later = comparison[pd.to_datetime(comparison.window_end_utc, utc=True) > last_holdout]
    new_sealed = "INSUFFICIENT_DATA" if later.group_key.nunique() < 25 else "AVAILABLE_NOT_CONSUMED"
    summary = {
        "policy": MODEL_COMPLEMENTARITY_POLICY, "status": STATUS,
        "frozen_inputs": {
            "statistical_model": "TERMINAL_PRICE_CONSERVATIVE_PROXY_NO_SQRT60",
            "reference_policy": "KALSHI_REFERENCE_V1",
            "ml_model": manifest["version"], "ml_candidate": manifest["frozen_candidate"],
            "model_hash": manifest["model_hash"], "feature_schema_hash": manifest["feature_schema_hash"],
            "data_hash": manifest["data_hash"], "config_hash": manifest["config_hash"],
            "freeze_manifest_hash": sha256_file(manifest_path)},
        "regime_thresholds_fitted_on_development_only": thresholds,
        "development": report_splits["development"],
        "validation": report_splits["validation"],
        "previously_opened_step5_holdout": {"label": OPENED_HOLDOUT_LABEL, **report_splits["sealed_holdout"]},
        "incremental_information_validation": incremental_information(
            comparison.loc[splits["development"]], comparison.loc[splits["validation"]]),
        "new_step6_sealed_sample": new_sealed,
        "new_rows_strictly_after_step5_holdout": int(len(later)),
        "new_windows_strictly_after_step5_holdout": int(later.group_key.nunique()),
        "step6_future_hypotheses": [
            "prospectively test near-target ML advantage in direction disagreements",
            "prospectively test shared high confidence beyond either confidence alone",
            "collect a genuinely new post-Step-5 sealed cohort before ensemble/veto research",
        ],
        "decision": "COMPLEMENTARITY_SUGGESTED_REQUIRES_FORWARD_VALIDATION",
        "fixed_step4_holdout_invariant": {"entries": 1377, "eligible": 2111,
                                             "coverage": expected_coverage},
        "prohibitions": {"ensemble_created": False, "gates_modified": False,
                         "live_selector_integration": False, "ui_integration": False,
                         "voice_integration": False, "execution_integration": False,
                         "position_management_integration": False},
        "limitations": ["YAHOO CURRENT VALUE IS A PROXY FOR CF BENCHMARKS",
                        "ENDING 60-SECOND AVERAGE IS APPROXIMATED",
                        "PREVIOUSLY OPENED STEP 5 HOLDOUT",
                        "LIMITED RECENT HISTORICAL REGIME",
                        "REPEATED STATE ROWS ARE CLUSTERED BY WINDOW",
                        "TRANSFERRED_UNVALIDATED_GATES", "RESEARCH SHADOW ONLY"],
    }
    output = ROOT / "data" / "kalshi_step6_summary.json"
    output.write_text(json.dumps(summary, indent=2, allow_nan=False, default=str), encoding="utf-8")
    print(json.dumps({"policy": summary["policy"], "status": summary["status"],
                      "rows": {k: summary[k]["rows"] for k in ("development", "validation")},
                      "opened_holdout_rows": summary["previously_opened_step5_holdout"]["rows"],
                      "new_step6_sealed_sample": new_sealed,
                      "output": str(output)}, indent=2))


if __name__ == "__main__":
    main()
