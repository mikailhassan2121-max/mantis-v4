"""Step 5: grouped chronological ML/calibration study (research only)."""
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
from mantis_v4.entry import apply_policy_once
from mantis_v4.models.dataset_builder import ModellingDatasetBuilder
from mantis_v4.models.kalshi_step5 import (
    FEATURE_CAUSALITY, FEATURE_NAMES, SEED, STEP5_MODEL_FAMILY,
    build_causal_features, chronological_group_split, fit_frozen_model,
    load_model, probability_metrics, save_model, schema_hash,
)
from scripts.kalshi_reference_study import cached_frames, contracts_from_csv, rows_for, target_crossings


def file_set_hash(paths):
    digest = hashlib.sha256()
    for path in sorted(map(Path, paths), key=lambda p: str(p)):
        digest.update(str(path.relative_to(ROOT)).replace("\\", "/").encode())
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    return digest.hexdigest()


def internal_development_masks(base_mask, window_end, fraction=.75):
    ends = pd.to_datetime(window_end[base_mask], utc=True)
    windows = sorted(ends.unique())
    cut = int(len(windows) * fraction)
    fit_windows, cal_windows = set(windows[:cut]), set(windows[cut:])
    fit = base_mask & pd.to_datetime(window_end, utc=True).isin(fit_windows)
    cal = base_mask & pd.to_datetime(window_end, utc=True).isin(cal_windows)
    return np.asarray(fit), np.asarray(cal)


def entry_mask(states, split_mask):
    subset = states.loc[split_mask]
    entries = apply_policy_once(subset, TRANSFERRED_POLICY)
    keys = set(zip(entries.loc[entries.entered, "contract_id"],
                   entries.loc[entries.entered, "asset"],
                   entries.loc[entries.entered, "seconds_remaining"]))
    return np.array([(row.contract_id, row.asset, row.seconds_remaining) in keys
                     for row in states.itertuples()]) & split_mask, entries


def split_description(mask, joined):
    rows = joined.loc[mask]
    ends = pd.to_datetime(rows.window_end_utc, utc=True)
    return {"rows": int(mask.sum()), "groups": int(rows.group_key.nunique()),
            "windows": int(ends.nunique()), "start_utc": ends.min().isoformat(),
            "end_utc": ends.max().isoformat()}


def main():
    market_path = ROOT / "data" / "kalshi_reference_v1_markets.csv"
    if not market_path.exists():
        raise SystemExit("Step 4 market artifact missing; run the bounded Step 4 study first")
    contracts = contracts_from_csv(market_path)
    assets = list(SERIES_BY_ASSET)
    frames = cached_frames(ROOT / "data" / "history", assets)
    if set(frames) != set(assets):
        raise SystemExit("cached causal history incomplete")
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
    joined["reference_gap_bps"] = ((joined.kalshi_target - joined.old_proxy_reference)
                                    / joined.old_proxy_reference * 10000)
    states = build_kalshi_states(joined)
    X = build_causal_features(joined, states)
    y = joined.outcome_yes.to_numpy(dtype=int)
    statistical = states.p_yes.to_numpy(dtype=float)

    splits = chronological_group_split(joined.group_key, joined.window_end_utc)
    dev, validation, holdout = (splits[x] for x in ("development", "validation", "sealed_holdout"))
    fit_mask, cal_mask = internal_development_masks(dev, joined.window_end_utc)
    if np.any(fit_mask & cal_mask) or np.any((dev | validation) & holdout):
        raise AssertionError("split leakage")

    validation_entry_mask, validation_entries = entry_mask(states, validation)
    candidates = []
    fitted = {}
    for kind in ("statistical", "logistic", "hist_gradient_boosting"):
        for calibration in ("identity", "platt", "isotonic"):
            model = fit_frozen_model(kind, calibration, X.loc[fit_mask], y[fit_mask],
                                     X.loc[cal_mask], y[cal_mask], statistical[fit_mask],
                                     statistical[cal_mask])
            p = model.predict(X.loc[validation], statistical[validation])
            all_metrics = probability_metrics(y[validation], p,
                                              assets=joined.loc[validation, "asset"])
            local_entry = validation_entry_mask[validation]
            entry_metrics = probability_metrics(y[validation], p,
                                                assets=joined.loc[validation, "asset"],
                                                entry_mask=local_entry)
            row = {"kind": kind, "calibration": calibration,
                   "validation_all_states": all_metrics,
                   "validation_fixed_step4_entry_cohort": entry_metrics,
                   "fixed_entry_coverage": float(validation_entries.entered.mean())}
            candidates.append(row); fitted[(kind, calibration)] = model

    # Precommitted selection rule: all-state Brier, then log loss, then simpler
    # declared order. Coverage is fixed and cannot influence this choice.
    order = {(k, c): i for i, (k, c) in enumerate(
        ([(k, c) for k in ("statistical", "logistic", "hist_gradient_boosting")
                    for c in ("identity", "platt", "isotonic")]))}
    chosen = min(candidates, key=lambda r: (
        r["validation_all_states"]["brier"], r["validation_all_states"]["log_loss"],
        order[(r["kind"], r["calibration"])]) )
    kind, calibration = chosen["kind"], chosen["calibration"]

    # Freeze final recipe: base model on development, calibrator on validation.
    final_model = fit_frozen_model(kind, calibration, X.loc[dev], y[dev],
                                   X.loc[validation], y[validation], statistical[dev],
                                   statistical[validation])
    model_path = ROOT / "data" / "models" / "kalshi_step5_frozen.joblib"
    model_hash = save_model(final_model, model_path)
    history_paths = [p for asset in assets for p in (ROOT / "data" / "history").glob(f"{asset}_1m_*.csv")]
    data_hash = file_set_hash([market_path, *history_paths])
    config = {"seed": SEED, "models": ["statistical", "logistic", "hist_gradient_boosting"],
              "calibrations": ["identity", "platt", "isotonic"],
              "selection_metric": ["validation_all_state_brier", "validation_all_state_log_loss"],
              "transferred_gates": TRANSFERRED_POLICY.__dict__,
              "live_actionability": False}
    config_hash = hashlib.sha256(json.dumps(config, sort_keys=True, default=str).encode()).hexdigest()
    freeze = {
        "version": STEP5_MODEL_FAMILY, "frozen_candidate": {"kind": kind, "calibration": calibration},
        "frozen_before_holdout": True, "data_hash": data_hash, "feature_schema_hash": schema_hash(),
        "model_hash": model_hash, "config_hash": config_hash, "random_seed": SEED,
        "features": list(FEATURE_NAMES), "feature_causality": FEATURE_CAUSALITY,
        "splits": {name: split_description(mask, joined) for name, mask in splits.items()},
        "internal_development": {"fit": split_description(fit_mask, joined),
                                 "calibration": split_description(cal_mask, joined)},
        "candidate_validation_results": candidates,
    }
    freeze_path = ROOT / "data" / "kalshi_step5_freeze.json"
    freeze_path.write_text(json.dumps(freeze, indent=2, allow_nan=False, default=str), encoding="utf-8")
    freeze_hash = hashlib.sha256(freeze_path.read_bytes()).hexdigest()

    # Holdout is first consumed only after the recipe/model/freeze manifest exist.
    frozen = load_model(model_path)
    p_candidate = frozen.predict(X.loc[holdout], statistical[holdout])
    p_baseline = statistical[holdout]
    holdout_entry_mask, holdout_entries = entry_mask(states, holdout)
    local_entry = holdout_entry_mask[holdout]
    baseline_all = probability_metrics(y[holdout], p_baseline, assets=joined.loc[holdout, "asset"])
    candidate_all = probability_metrics(y[holdout], p_candidate, assets=joined.loc[holdout, "asset"])
    baseline_entry = probability_metrics(y[holdout], p_baseline,
                                         assets=joined.loc[holdout, "asset"], entry_mask=local_entry)
    candidate_entry = probability_metrics(y[holdout], p_candidate,
                                          assets=joined.loc[holdout, "asset"], entry_mask=local_entry)
    summary = {
        "version": STEP5_MODEL_FAMILY, "status": "RESEARCH_SHADOW_ONLY",
        "frozen_candidate": {"kind": kind, "calibration": calibration},
        "freeze_manifest_hash": freeze_hash, "model_hash": model_hash,
        "data_hash": data_hash, "feature_schema_hash": schema_hash(), "config_hash": config_hash,
        "validation_selection": chosen,
        "sealed_holdout": {
            "fixed_step4_entry_coverage": float(holdout_entries.entered.mean()),
            "baseline_all_states": baseline_all, "candidate_all_states": candidate_all,
            "baseline_fixed_step4_entry_cohort": baseline_entry,
            "candidate_fixed_step4_entry_cohort": candidate_entry,
            "all_state_delta_candidate_minus_baseline": {
                "brier": candidate_all["brier"] - baseline_all["brier"],
                "log_loss": candidate_all["log_loss"] - baseline_all["log_loss"],
                "accuracy": candidate_all["accuracy"] - baseline_all["accuracy"]},
            "entry_cohort_delta_candidate_minus_baseline": {
                "brier": candidate_entry["brier"] - baseline_entry["brier"],
                "log_loss": candidate_entry["log_loss"] - baseline_entry["log_loss"],
                "accuracy": candidate_entry["accuracy"] - baseline_entry["accuracy"]},
        },
        "limitations": ["YAHOO CURRENT VALUE IS A PROXY FOR CF BENCHMARKS",
                        "60-SECOND ENDPOINT AVERAGE IS APPROXIMATED",
                        "TRANSFERRED_UNVALIDATED_GATES", "NO LIVE ACTIONABILITY"],
    }
    (ROOT / "data" / "kalshi_step5_summary.json").write_text(
        json.dumps(summary, indent=2, allow_nan=False, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=2, allow_nan=False, default=str))


if __name__ == "__main__":
    main()
