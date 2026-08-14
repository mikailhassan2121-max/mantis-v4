#!/usr/bin/env python3
# ================================================================
# MANTIS V4 — PHASE 4 RUNNER
#
# Transparent statistical probability models for terminal 15-minute
# outcomes, with calibration as the primary criterion.
#
#   PROXY SETTLEMENT REFERENCE
#   NO HISTORICAL WEBULL CONTRACT QUOTES
#   CLASSIFICATION RESEARCH ONLY - NOT A PROFITABILITY BACKTEST
#   LIMITED RECENT HISTORICAL REGIME
#
# V3 is never touched.
# ================================================================

from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path
from typing import Optional

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
warnings.filterwarnings("ignore", category=FutureWarning)

import numpy as np                                                # noqa: E402
import pandas as pd                                               # noqa: E402

from mantis_v4.backtest import YFinanceHistoryProvider, load_universe   # noqa: E402
from mantis_v4.clock import load_timezone                         # noqa: E402
from mantis_v4.config import PROJECT_ROOT, MantisConfig           # noqa: E402
from mantis_v4.models import (                                    # noqa: E402
    ALL_FEATURES,
    DECISION_THRESHOLDS,
    ENTRY_TIME_BUCKETS,
    FEATURE_FAMILIES,
    LogisticModel,
    ModellingDatasetBuilder,
    NormalZModel,
    ProbitModel,
    assess,
    bucket_by_entry_time,
    clustered_accuracy,
    drop_incomplete,
    evaluate_slice,
    fit_final_model,
    grouped_walk_forward,
    paired_bootstrap_difference,
    reserve_holdout,
    run_walk_forward,
    save_coefficients,
    save_model,
    select_usable_features,
    selective_prediction,
    summarise_splits,
)

SEP = "=" * 78
BANNER_LIMITS = [
    "PROXY SETTLEMENT REFERENCE",
    "NO HISTORICAL WEBULL CONTRACT QUOTES",
    "CLASSIFICATION RESEARCH ONLY - NOT A PROFITABILITY BACKTEST",
    "LIMITED RECENT HISTORICAL REGIME",
]

SEED = 20260813


def f(value, digits=4, dash="N/A") -> str:
    if value is None:
        return dash
    if isinstance(value, float) and (value != value or not np.isfinite(value)):
        return dash
    return f"{value:.{digits}f}"


def pct(value, digits=2, dash="N/A") -> str:
    if value is None or (isinstance(value, float) and (value != value or not np.isfinite(value))):
        return dash
    return f"{value * 100:.{digits}f}%"


def print_limits() -> None:
    print(SEP)
    for line in BANNER_LIMITS:
        print(f"  {line}")
    print(SEP)


# ---------------------------------------------------------------------------

def build_dataset(config, args) -> tuple[pd.DataFrame, dict]:
    timezone = load_timezone(config.contract_timezone)
    provider = YFinanceHistoryProvider(
        PROJECT_ROOT / "data" / "history",
        timeout_seconds=config.network_timeout_seconds,
    )
    frames, reports = load_universe(provider, config.active_assets, args.days)
    for report in reports:
        print(f"  {report.summary()}")
    if not frames:
        raise SystemExit("no asset had adequate data")

    builder = ModellingDatasetBuilder(frames, timezone)
    table, build_report = builder.build()
    print(f"\n  {build_report.summary()}")

    # Drop unusable FEATURES before dropping incomplete ROWS. Some features are
    # structurally undefined early in a contract; satisfying them by deleting
    # rows would strip out early-contract scans and bias the entry-time
    # analysis toward late entries.
    usable, dropped_features = select_usable_features(table, ALL_FEATURES)
    if dropped_features:
        print("\n  FEATURES DROPPED (missing too often to keep; rows preserved):")
        for name, rate in sorted(dropped_features.items(), key=lambda kv: -kv[1]):
            print(f"    {name:<28} {rate * 100:5.1f}% missing")

    cleaned, clean_report = drop_incomplete(table, usable)
    print(f"\n  features retained : {len(usable)} of {len(ALL_FEATURES)}")
    print(f"  complete-case rows: {clean_report['kept']} "
          f"(dropped {clean_report['dropped']} rows with any remaining non-finite value)")

    meta = {
        "contracts_used": build_report.contracts_used,
        "rows_raw": build_report.rows,
        "rows_complete": clean_report["kept"],
        "rows_dropped": clean_report["dropped"],
        "features_retained": usable,
        "features_dropped": dropped_features,
        "assets": sorted(frames),
    }
    return cleaned, meta


def feature_sets(available: list[str]) -> dict[str, list[str]]:
    """Ablation ladder: Normal-Z alone, then each family added to geometry.

    Restricted to features that survived cleaning. A feature dropped for
    structural missingness must not be silently reintroduced here -- that is
    exactly what crashed the first full run.
    """
    keep = set(available)
    geometry = [f for f in FEATURE_FAMILIES["geometry"] if f in keep]
    sets = {
        "normal_z_only": ["normal_z"],
        "geometry": list(geometry),
    }
    for family in ("momentum", "volatility", "structure", "cross_asset", "time"):
        extra = [f for f in FEATURE_FAMILIES[family] if f in keep]
        if extra:
            sets[f"geometry+{family}"] = list(geometry) + extra
    sets["all_features"] = list(available)
    return sets


def model_factories(seed: int = SEED) -> dict:
    return {
        "A_normal_z": NormalZModel,
        "B_logistic": lambda: LogisticModel("B_logistic", penalty=None, seed=seed),
        "C_logistic_l2": lambda: LogisticModel("C_logistic_l2", penalty="l2", C=1.0, seed=seed),
        "C_logistic_l1": lambda: LogisticModel("C_logistic_l1", penalty="l1", C=0.5, seed=seed),
        "D_probit": lambda: ProbitModel("D_probit", seed=seed),
    }


# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="mantis_v4_phase4")
    parser.add_argument("--days", type=int, default=28)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--holdout", type=float, default=0.25)
    parser.add_argument("--boot", type=int, default=600)
    parser.add_argument("--quick", action="store_true",
                        help="fewer bootstrap resamples, for a fast smoke run")
    args = parser.parse_args(argv)
    if args.quick:
        args.boot = 120

    config = MantisConfig.load()
    out_dir = PROJECT_ROOT / "data"
    models_dir = PROJECT_ROOT / "models"
    models_dir.mkdir(parents=True, exist_ok=True)

    print(SEP)
    print("  MANTIS V4 - PHASE 4: TRANSPARENT PROBABILITY MODELS")
    print(SEP)
    print_limits()

    # -- 1. dataset ---------------------------------------------------------
    print("\n[1/7] BUILDING MODELLING DATASET\n")
    table, data_meta = build_dataset(config, args)
    # The authoritative feature list for the rest of the run. Using
    # ALL_FEATURES here would reintroduce anything cleaning removed.
    FEATURES: list[str] = data_meta["features_retained"]

    # -- 2. holdout ---------------------------------------------------------
    print("\n[2/7] RESERVING SEALED HOLDOUT\n")
    plan = reserve_holdout(table, holdout_fraction=args.holdout)
    described = plan.describe()
    for key, value in described.items():
        print(f"  {key:<20} {value}")
    print("\n  The holdout is used ONCE, at the end. It is not used for feature")
    print("  selection, calibrator selection, or threshold tuning.")

    splits = grouped_walk_forward(plan.dev, folds=args.folds)
    print(f"\n  walk-forward folds on the development set ({len(splits)}):")
    for row in summarise_splits(splits):
        print(f"    fold {row['fold']}: train {row['train_rows']:>7} rows / "
              f"{row['train_groups']:>5} windows   test {row['test_rows']:>7} rows / "
              f"{row['test_groups']:>5} windows   purged {row['purged_groups']}")

    # -- 3. models on the full feature set ----------------------------------
    print("\n[3/7] MODEL COMPARISON (walk-forward, out-of-sample)\n")
    factories = model_factories()
    results = {}
    baseline_key = "A_normal_z"

    for name, factory in factories.items():
        features = ["normal_z"] if name == baseline_key else FEATURES
        result = run_walk_forward(
            plan.dev, features, factory, splits=splits,
            model_name=name, feature_set="normal_z" if name == baseline_key else "all_features",
        )
        results[name] = result
        raw = assess(result.p_raw, result.y, f"{name}/raw")
        cal = assess(result.p_calibrated, result.y, f"{name}/cal")
        acc = clustered_accuracy(
            np.where(result.p_calibrated >= 0.5, result.y == 1, result.y == 0).astype(float),
            result.groups, n_boot=args.boot, seed=SEED,
        )
        print(f"  {name}")
        print(f"    n={result.n}  windows={len(np.unique(result.groups))}  "
              f"calibrator votes={result.calibrator_votes}")
        print(f"    RAW  brier={f(raw.brier)}  logloss={f(raw.logloss)}  "
              f"slope={f(raw.slope,3)}  ECE={f(raw.ece,4)}  [{raw.verdict}]")
        print(f"    CAL  brier={f(cal.brier)}  logloss={f(cal.logloss)}  "
              f"slope={f(cal.slope,3)}  ECE={f(cal.ece,4)}  [{cal.verdict}]")
        print(f"    accuracy={pct(acc.point)}  clustered 95% CI "
              f"[{pct(acc.low)}, {pct(acc.high)}]  "
              f"(naive [{pct(acc.naive_low)}, {pct(acc.naive_high)}], "
              f"{f(acc.inflation,2)}x wider)\n")

    # -- 4. paired comparison vs Normal-Z -----------------------------------
    print("\n[4/7] PAIRED COMPARISON AGAINST NORMAL-Z (clustered bootstrap)\n")
    base = results[baseline_key]
    base_lookup = dict(zip(base.test_idx, base.p_calibrated))
    comparisons = {}
    print(f"  {'model':<16}{'dBrier':>12}{'95% CI':>26}{'verdict':>28}")
    print("  " + "-" * 82)
    for name, result in results.items():
        if name == baseline_key:
            continue
        common = np.array([i for i in result.test_idx if i in base_lookup])
        if len(common) == 0:
            continue
        order = {idx: k for k, idx in enumerate(result.test_idx)}
        pos = np.array([order[i] for i in common])
        p_model = result.p_calibrated[pos]
        p_base = np.array([base_lookup[i] for i in common])
        y = result.y[pos]
        groups = result.groups[pos]

        point, low, high = paired_bootstrap_difference(
            p_model, p_base, y, groups, "brier", n_boot=args.boot, seed=SEED
        )
        if np.isfinite(high) and high < 0:
            verdict = "BEATS Normal-Z"
        elif np.isfinite(low) and low > 0:
            verdict = "WORSE than Normal-Z"
        else:
            verdict = "NOT DISTINGUISHABLE"
        comparisons[name] = {"delta_brier": point, "ci_low": low, "ci_high": high,
                             "verdict": verdict, "n": int(len(common))}
        print(f"  {name:<16}{f(point,5):>12}"
              f"{f'[{f(low,5)}, {f(high,5)}]':>26}{verdict:>28}")
    print("\n  Negative dBrier = better than Normal-Z. A CI spanning zero means")
    print("  the difference is not distinguishable from noise on this data.")

    # -- 5. feature ablation ------------------------------------------------
    print("\n[5/7] FEATURE ABLATION (logistic L2, walk-forward)\n")
    ablation = {}
    print(f"  {'feature set':<22}{'k':>4}{'brier':>10}{'logloss':>10}"
          f"{'slope':>9}{'ECE':>9}{'accuracy':>11}")
    print("  " + "-" * 75)
    for label, features in feature_sets(FEATURES).items():
        factory = (
            NormalZModel if label == "normal_z_only"
            else (lambda: LogisticModel(f"l2_{label}", penalty="l2", C=1.0, seed=SEED))
        )
        result = run_walk_forward(
            plan.dev, features, factory, splits=splits,
            model_name=label, feature_set=label,
        )
        cal = assess(result.p_calibrated, result.y, label)
        won = np.where(result.p_calibrated >= 0.5, result.y == 1, result.y == 0).astype(float)
        accuracy = float(won.mean())
        ablation[label] = {
            "k_features": len(features), "brier": cal.brier, "logloss": cal.logloss,
            "slope": cal.slope, "ece": cal.ece, "accuracy": accuracy, "n": result.n,
        }
        print(f"  {label:<22}{len(features):>4}{f(cal.brier):>10}{f(cal.logloss):>10}"
              f"{f(cal.slope,3):>9}{f(cal.ece,4):>9}{pct(accuracy):>11}")

    # -- 6. pick the simplest model that beats Normal-Z ---------------------
    print("\n[6/7] SELECTING THE SIMPLEST MODEL THAT ROBUSTLY BEATS NORMAL-Z\n")
    winners = [n for n, c in comparisons.items() if c["verdict"] == "BEATS Normal-Z"]
    if winners:
        chosen = sorted(winners)[0]
        print(f"  candidates that beat Normal-Z: {', '.join(sorted(winners))}")
        print(f"  selected: {chosen}")
    else:
        chosen = baseline_key
        print("  NOTHING BEAT NORMAL-Z on the development folds.")
        print("  Keeping Normal-Z as the Phase 4 model, per section 12 item 7.")

    chosen_features = ["normal_z"] if chosen == baseline_key else FEATURES
    chosen_factory = factories[chosen]

    # -- 7. selective prediction, entry time, and the sealed holdout --------
    print("\n[7/7] SELECTIVE PREDICTION AND SEALED-HOLDOUT EVALUATION\n")
    chosen_result = results[chosen]

    print("  SELECTIVE PREDICTION (development folds, out-of-sample)")
    print(f"  {'thresh':>7}{'coverage':>10}{'trades':>9}{'windows':>9}{'accuracy':>10}"
          f"{'clustered 95% CI':>22}{'YES acc':>9}{'NO acc':>9}")
    print("  " + "-" * 87)
    dev_selective = selective_prediction(
        chosen_result.p_calibrated, chosen_result.y, chosen_result.groups,
        n_boot=args.boot, seed=SEED,
    )
    for row in dev_selective:
        ci = (f"[{pct(row.ci_low,1)}, {pct(row.ci_high,1)}]"
              if np.isfinite(row.ci_low) else "N/A")
        print(f"  {row.threshold:>7.2f}{pct(row.coverage,1):>10}{row.n_trades:>9}"
              f"{row.n_groups:>9}{pct(row.accuracy,1):>10}{ci:>22}"
              f"{pct(row.yes_accuracy,1):>9}{pct(row.no_accuracy,1):>9}")

    print("\n  ENTRY-TIME ANALYSIS (does later simply mean a bigger buffer?)")
    print(f"  {'bucket':<12}{'n':>8}{'accuracy':>10}{'clustered CI':>22}"
          f"{'brier':>9}{'mean |z|':>10}")
    print("  " + "-" * 71)
    dev_buckets = bucket_by_entry_time(
        plan.dev.iloc[chosen_result.test_idx]["seconds_remaining"].to_numpy()
    )
    abs_z = np.abs(plan.dev.iloc[chosen_result.test_idx]["normal_z"].to_numpy())
    entry_rows = {}
    for name, _, _ in ENTRY_TIME_BUCKETS:
        mask = dev_buckets == name
        if mask.sum() == 0:
            continue
        s = evaluate_slice(
            chosen_result.p_calibrated[mask], chosen_result.y[mask],
            chosen_result.groups[mask], name, abs_z=abs_z[mask],
            n_boot=max(120, args.boot // 3), seed=SEED,
        )
        entry_rows[name] = s.as_row()
        print(f"  {name:<12}{s.n:>8}{pct(s.accuracy,1):>10}"
              f"{f'[{pct(s.ci_low,1)}, {pct(s.ci_high,1)}]':>22}"
              f"{f(s.brier):>9}{f(s.mean_abs_z,3):>10}")

    # ---- the holdout, used once -------------------------------------------
    print("\n  " + "-" * 74)
    print("  SEALED HOLDOUT - EVALUATED ONCE, NOW")
    print("  " + "-" * 74)

    final_model, final_calibrator, fit_meta = fit_final_model(
        plan.dev, chosen_features, chosen_factory
    )
    plan.seal.assert_disjoint(set(plan.dev["group_key"]), "final fit data")

    X_hold = plan.holdout[chosen_features].to_numpy(dtype="float64")
    y_hold = plan.holdout["outcome_yes"].to_numpy(dtype="int64")
    g_hold = plan.holdout["group_key"].to_numpy()

    p_hold_raw = final_model.predict_proba(X_hold)
    p_hold = final_calibrator.transform(p_hold_raw)

    hold_raw = assess(p_hold_raw, y_hold, "holdout/raw")
    hold_cal = assess(p_hold, y_hold, "holdout/calibrated")
    hold_acc = clustered_accuracy(
        np.where(p_hold >= 0.5, y_hold == 1, y_hold == 0).astype(float),
        g_hold, n_boot=args.boot, seed=SEED,
    )

    # Normal-Z on the same holdout rows, for a like-for-like comparison.
    zmodel = NormalZModel().fit(
        plan.holdout[["normal_z"]].to_numpy(dtype="float64"), y_hold, ["normal_z"]
    )
    p_hold_z = zmodel.predict_proba(plan.holdout[["normal_z"]].to_numpy(dtype="float64"))
    hold_z = assess(p_hold_z, y_hold, "holdout/normal_z")
    z_acc = clustered_accuracy(
        np.where(p_hold_z >= 0.5, y_hold == 1, y_hold == 0).astype(float),
        g_hold, n_boot=args.boot, seed=SEED,
    )

    print(f"  model on holdout    : {chosen}  (calibrator: {fit_meta['calibrator']})")
    print(f"  holdout rows        : {len(y_hold)}  windows: {len(np.unique(g_hold))}")
    print(f"  {chosen:<20} brier={f(hold_cal.brier)}  logloss={f(hold_cal.logloss)}  "
          f"slope={f(hold_cal.slope,3)}  ECE={f(hold_cal.ece,4)}")
    print(f"  {'  accuracy':<20} {pct(hold_acc.point)}  "
          f"clustered CI [{pct(hold_acc.low)}, {pct(hold_acc.high)}]")
    print(f"  {'A_normal_z':<20} brier={f(hold_z.brier)}  logloss={f(hold_z.logloss)}  "
          f"slope={f(hold_z.slope,3)}  ECE={f(hold_z.ece,4)}")
    print(f"  {'  accuracy':<20} {pct(z_acc.point)}  "
          f"clustered CI [{pct(z_acc.low)}, {pct(z_acc.high)}]")

    hp, hlo, hhi = paired_bootstrap_difference(
        p_hold, p_hold_z, y_hold, g_hold, "brier", n_boot=args.boot, seed=SEED
    )
    if np.isfinite(hhi) and hhi < 0:
        hold_verdict = "BEATS Normal-Z on the sealed holdout"
    elif np.isfinite(hlo) and hlo > 0:
        hold_verdict = "WORSE than Normal-Z on the sealed holdout"
    else:
        hold_verdict = "NOT DISTINGUISHABLE from Normal-Z on the sealed holdout"
    print(f"\n  dBrier vs Normal-Z  : {f(hp,5)}  95% CI [{f(hlo,5)}, {f(hhi,5)}]")
    print(f"  VERDICT             : {hold_verdict}")

    print("\n  HOLDOUT RELIABILITY (calibrated)")
    print(f"  {'bucket':<12}{'n':>8}{'predicted':>12}{'observed':>11}{'95% CI':>20}{'gap':>9}")
    print("  " + "-" * 72)
    for b in hold_cal.buckets:
        if b.count == 0:
            continue
        print(f"  {f'{b.low:.2f}-{b.high:.2f}':<12}{b.count:>8}"
              f"{pct(b.mean_predicted,1):>12}{pct(b.observed_rate,1):>11}"
              f"{f'[{pct(b.ci_low,1)}, {pct(b.ci_high,1)}]':>20}"
              f"{b.gap * 100:>+8.1f}p")

    hold_selective = selective_prediction(
        p_hold, y_hold, g_hold, n_boot=args.boot, seed=SEED
    )
    print("\n  HOLDOUT SELECTIVE PREDICTION")
    print(f"  {'thresh':>7}{'coverage':>10}{'trades':>9}{'accuracy':>10}{'clustered 95% CI':>22}")
    print("  " + "-" * 60)
    for row in hold_selective:
        ci = (f"[{pct(row.ci_low,1)}, {pct(row.ci_high,1)}]"
              if np.isfinite(row.ci_low) else "N/A")
        print(f"  {row.threshold:>7.2f}{pct(row.coverage,1):>10}{row.n_trades:>9}"
              f"{pct(row.accuracy,1):>10}{ci:>22}")

    # -- persist -------------------------------------------------------------
    save_model(final_model, models_dir / f"phase4_{chosen}.pkl")
    save_coefficients(final_model, models_dir / f"phase4_{chosen}_coefficients.json")

    summary = {
        "limitations": BANNER_LIMITS,
        "data": data_meta,
        "holdout": described,
        "splits": summarise_splits(splits),
        "models": {
            name: {
                "n": r.n,
                "calibrator_votes": r.calibrator_votes,
                "raw": assess(r.p_raw, r.y, name).as_row(),
                "calibrated": assess(r.p_calibrated, r.y, name).as_row(),
            }
            for name, r in results.items()
        },
        "vs_normal_z": comparisons,
        "ablation": ablation,
        "chosen_model": chosen,
        "dev_selective": [r.as_row() for r in dev_selective],
        "entry_time": entry_rows,
        "holdout_result": {
            "model": chosen,
            "calibrator": fit_meta["calibrator"],
            "rows": int(len(y_hold)),
            "windows": int(len(np.unique(g_hold))),
            "calibrated": hold_cal.as_row(),
            "raw": hold_raw.as_row(),
            "normal_z": hold_z.as_row(),
            "accuracy": hold_acc.point,
            "accuracy_ci": [hold_acc.low, hold_acc.high],
            "normal_z_accuracy": z_acc.point,
            "normal_z_accuracy_ci": [z_acc.low, z_acc.high],
            "delta_brier_vs_normal_z": hp,
            "delta_brier_ci": [hlo, hhi],
            "verdict": hold_verdict,
            "reliability": [
                {
                    "low": b.low, "high": b.high, "n": b.count,
                    "predicted": b.mean_predicted, "observed": b.observed_rate,
                    "ci_low": b.ci_low, "ci_high": b.ci_high, "gap": b.gap,
                }
                for b in hold_cal.buckets if b.count > 0
            ],
            "selective": [r.as_row() for r in hold_selective],
        },
        "seal_intact": plan.seal.intact,
        "economic_profitability": "NOT EVALUABLE - no historical contract prices",
    }
    path = out_dir / "mantis_v4_phase4_summary.json"
    path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(f"\n  summary written: {path}")

    print()
    print_limits()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
