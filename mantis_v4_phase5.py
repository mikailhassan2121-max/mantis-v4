#!/usr/bin/env python3
# ================================================================
# MANTIS V4 — PHASE 5 RUNNER
#
# Terminal-distribution, Monte Carlo, bootstrap, sensitivity and
# fragility models built AROUND the validated Normal-Z anchor.
#
#   PROXY SETTLEMENT REFERENCE
#   NO HISTORICAL WEBULL CONTRACT QUOTES
#   CLASSIFICATION RESEARCH ONLY - NOT A PROFITABILITY BACKTEST
#   LIMITED RECENT HISTORICAL REGIME
#
# Normal-Z is NOT replaced unless a challenger robustly beats it.
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
warnings.filterwarnings("ignore", category=UserWarning)

import numpy as np                                                   # noqa: E402
import pandas as pd                                                  # noqa: E402

from mantis_v4.backtest import YFinanceHistoryProvider, load_universe  # noqa: E402
from mantis_v4.backtest.features import precompute_indicators          # noqa: E402
from mantis_v4.clock import load_timezone                             # noqa: E402
from mantis_v4.config import PROJECT_ROOT, MantisConfig               # noqa: E402
from mantis_v4.models import (                                        # noqa: E402
    ALL_FEATURES,
    ModellingDatasetBuilder,
    assess,
    clustered_accuracy,
    clustered_metric,
    drop_incomplete,
    grouped_walk_forward,
    paired_bootstrap_difference,
    reserve_holdout,
    select_usable_features,
    selective_prediction,
)
from mantis_v4.models.evaluation import ENTRY_TIME_BUCKETS, bucket_by_entry_time  # noqa: E402
from mantis_v4.models.features_extended import precompute_extended    # noqa: E402
from mantis_v4.simulation import (                                    # noqa: E402
    BANDS,
    EmpiricalBootstrapModel,
    EmpiricalTerminalModel,
    MonteCarloConfig,
    MonteCarloEngine,
    analyse_normality,
    build_estimators,
    combine_channels,
    compute_fragility,
    conservative_lower_bound,
    digital_sensitivities,
    disagreement_error_study,
    fit_student_t_df,
    gaussian_terminal,
    scaled_sensitivities,
    sigma_remaining_from,
    standardized_terminal_returns,
    student_t_terminal,
    volatility_clustering,
    volatility_of_volatility,
)

SEP = "=" * 78
LIMITS = [
    "PROXY SETTLEMENT REFERENCE",
    "NO HISTORICAL WEBULL CONTRACT QUOTES",
    "CLASSIFICATION RESEARCH ONLY - NOT A PROFITABILITY BACKTEST",
    "LIMITED RECENT HISTORICAL REGIME",
]
SEED = 20260813


def f(v, d=4, dash="N/A"):
    if v is None:
        return dash
    if isinstance(v, float) and (v != v or not np.isfinite(v)):
        return dash
    return f"{v:.{d}f}"


def pct(v, d=2, dash="N/A"):
    if v is None or (isinstance(v, float) and (v != v or not np.isfinite(v))):
        return dash
    return f"{v * 100:.{d}f}%"


def banner():
    print(SEP)
    for line in LIMITS:
        print(f"  {line}")
    print(SEP)


def quantiles(values, qs=(0.05, 0.50, 0.95)) -> dict[str, float]:
    """Finite-only quantiles for compact, JSON-safe diagnostics."""
    values = np.asarray(values, dtype="float64")
    values = values[np.isfinite(values)]
    if not len(values):
        return {f"q{int(q * 100):02d}": float("nan") for q in qs}
    return {f"q{int(q * 100):02d}": float(np.quantile(values, q)) for q in qs}


def write_markdown_report(path: Path, summary: dict) -> None:
    """Write the final human-readable Phase 5 report from the JSON results."""
    lines = [
        "# MANTIS — PHASE 5 RESULTS",
        "",
        "```",
        "PROXY SETTLEMENT REFERENCE",
        "NO HISTORICAL WEBULL CONTRACT QUOTES",
        "CLASSIFICATION RESEARCH ONLY",
        "NOT A PROFITABILITY BACKTEST",
        "LIMITED RECENT HISTORICAL REGIME",
        "```",
        "",
        "## Headline",
        "",
        summary["conclusion"],
        "",
        "The chronological holdout remained sealed and was not used for Phase 5 tuning.",
        "NO TRADE remains an abstention, not an incorrect prediction.",
        "",
        "## Model results",
        "",
        "| Model | n | Brier | Log loss | Slope | ECE | Accuracy | Clustered 95% CI |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for name, row in summary.get("models", {}).items():
        lines.append(
            f"| {name} | {row['n']} | {row['brier']:.5f} | {row['logloss']:.5f} | "
            f"{row['calibration_slope']:.3f} | {row['ece']:.4f} | {row['accuracy']:.2%} | "
            f"[{row['ci_low']:.2%}, {row['ci_high']:.2%}] |"
        )
    lines += ["", "### Paired Brier comparisons against Normal-Z", "",
              "| Challenger | n | ΔBrier | Clustered 95% CI | Verdict |",
              "|---|---:|---:|---|---|"]
    for name, row in summary.get("vs_normal_z", {}).items():
        lines.append(
            f"| {name} | {row['n']} | {row['delta_brier']:+.6f} | "
            f"[{row['ci'][0]:+.6f}, {row['ci'][1]:+.6f}] | {row['verdict']} |"
        )
    lines += [
        "",
        "## Monte Carlo, terminal distributions, and uncertainty",
        "",
        f"Monte Carlo configuration/results: `{json.dumps(summary.get('monte_carlo', {}), default=str)}`",
        "",
        f"Student-t terminal diagnostics: `{json.dumps(summary.get('student_t_terminal', {}), default=str)}`",
        "",
        f"Conservative-bound slices: `{json.dumps(summary.get('conservative_bounds', {}), default=str)}`",
        "",
        "## Heavy tails and volatility",
        "",
        f"Normality verdict: **{summary.get('normality', {}).get('verdict', 'N/A')}**",
        "",
        f"Student-t fit: `{json.dumps(summary.get('student_t_fit', {}), default=str)}`",
        "",
        "Volatility estimators and paired comparisons are recorded in the JSON output under "
        "`volatility` and `volatility_vs_validated`.",
        "",
        "## Fragility, disagreement, and dependence",
        "",
        f"Fragility results: `{json.dumps(summary.get('fragility', {}), default=str)}`",
        "",
        f"Disagreement study: `{json.dumps(summary.get('agreement', {}).get('study', {}), default=str)}`",
        "",
        f"Cross-asset dependence: `{json.dumps(summary.get('cross_asset_dependence', {}), default=str)}`",
        "",
        "## Components that survive into Phase 6",
        "",
    ]
    for item in summary.get("survival_recommendations", []):
        lines.append(f"- {item}")
    lines += ["", "## Limitations", ""]
    for item in summary.get("detailed_limitations", []):
        lines.append(f"- {item}")
    lines += ["", "Phase 5 stops here. Phase 6 requires explicit approval.", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="mantis_v4_phase5")
    parser.add_argument("--days", type=int, default=28)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--holdout", type=float, default=0.25)
    parser.add_argument("--mc-paths", type=int, default=2000)
    parser.add_argument("--mc-sample", type=int, default=40000,
                        help="rows sampled for the Monte Carlo study")
    parser.add_argument("--boot", type=int, default=400)
    args = parser.parse_args(argv)

    config = MantisConfig.load()
    timezone = load_timezone(config.contract_timezone)
    out_dir = PROJECT_ROOT / "data"
    rng = np.random.default_rng(SEED)

    print(SEP)
    print("  MANTIS V4 - PHASE 5: SIMULATION, SENSITIVITY, FRAGILITY")
    print(SEP)
    banner()
    print("\n  Normal-Z is the ANCHOR. Challengers replace it only on robust")
    print("  out-of-sample improvement (Phase 5 section 1).\n")

    # ---------------------------------------------------------------- data
    print("[1/9] DATASET\n")
    provider = YFinanceHistoryProvider(out_dir / "history",
                                       timeout_seconds=config.network_timeout_seconds)
    frames, load_reports = load_universe(provider, config.active_assets, args.days)
    for r in load_reports:
        print(f"  {r.summary()}")
    if not frames:
        raise SystemExit("no asset had adequate data")

    builder = ModellingDatasetBuilder(frames, timezone)
    table, build_report = builder.build()
    usable, dropped = select_usable_features(table, ALL_FEATURES)
    table, clean = drop_incomplete(table, usable)
    print(f"\n  {build_report.summary()}")
    print(f"  rows retained: {clean['kept']}   features: {len(usable)}"
          f"   dropped features: {sorted(dropped)}")

    plan = reserve_holdout(table, holdout_fraction=args.holdout)
    splits = grouped_walk_forward(plan.dev, folds=args.folds)
    print(f"  dev {len(plan.dev)} rows / {plan.dev.group_key.nunique()} windows;"
          f"  holdout {len(plan.holdout)} rows / {plan.holdout.group_key.nunique()} windows")
    print(f"  holdout hash {plan.seal.group_hash}  seal_intact={plan.seal.intact}")

    # Evaluation set = pooled walk-forward TEST rows (never trained on).
    test_idx = np.concatenate([s.test_idx for s in splits])
    train_idx = splits[0].train_idx          # earliest block, used to FIT pools
    dev_eval = plan.dev.iloc[test_idx].reset_index(drop=True)
    dev_fit = plan.dev.iloc[np.concatenate([s.train_idx for s in splits[:1]])]

    # Pools for the empirical/bootstrap channels come from the earliest
    # training block only, so nothing they learn touches an evaluation row.
    fit_block = plan.dev.iloc[train_idx]
    print(f"  empirical/bootstrap pools fitted on {len(fit_block)} earliest-block rows")

    summary: dict = {"limitations": LIMITS,
                     "data": {"rows": int(len(table)), "dev": int(len(plan.dev)),
                              "holdout": int(len(plan.holdout)),
                              "features_dropped": dropped},
                     "holdout": plan.describe()}

    # -------------------------------------------------------- normality
    print("\n[2/9] NORMALITY / HEAVY-TAIL STRESS TEST\n")
    z_train = standardized_terminal_returns(
        fit_block.spot.to_numpy(), fit_block.terminal_price.to_numpy(),
        fit_block.sigma_remaining_return.to_numpy())
    norm_report = analyse_normality(z_train)
    print(f"  n={norm_report.n}  mean={f(norm_report.mean,3)}  sd={f(norm_report.std,3)}")
    print(f"  skew={f(norm_report.skew,3)}  excess kurtosis={f(norm_report.excess_kurtosis,3)}")
    print(f"  Jarque-Bera={f(norm_report.jarque_bera,1)}  p={norm_report.jarque_bera_p:.3e}")
    print(f"  |z|>4 rate={pct(norm_report.jump_rate_4sigma,3)}")
    print(f"  VERDICT: {norm_report.verdict}\n")
    print(f"  {'z':>7}{'Gaussian tail':>16}{'observed':>12}{'ratio':>9}")
    print("  " + "-" * 44)
    for row in norm_report.tail_table:
        print(f"  {row['z']:>7.4f}{row['gaussian_tail']:>16.5f}"
              f"{row['observed_mean']:>12.5f}{row['ratio_observed_over_gaussian']:>9.2f}")

    t_fit = fit_student_t_df(z_train)
    if t_fit["status"] == "OK":
        print(f"\n  Student-t MLE df = {t_fit['df']:.2f}  ({t_fit['interpretation']})")
    one_min = precompute_indicators(frames[sorted(frames)[0]])
    acf = volatility_clustering(np.diff(np.log(one_min["close"].to_numpy())))
    print(f"  volatility clustering acf(1) of |1m returns| = {f(acf,4)}")
    summary["normality"] = norm_report.as_dict()
    summary["student_t_fit"] = t_fit
    summary["vol_clustering_acf1"] = acf

    nu = t_fit["df"] if t_fit["status"] == "OK" else 5.0

    # ------------------------------------------------- channel construction
    print("\n[3/9] BUILDING PROBABILITY CHANNELS ON THE EVALUATION SET\n")
    buffer = dev_eval.buffer_pct.to_numpy()
    sigma_rem = dev_eval.sigma_remaining_return.to_numpy()
    spot = dev_eval.spot.to_numpy()
    reference = dev_eval.reference.to_numpy()
    seconds = dev_eval.seconds_remaining.to_numpy()
    sigma_1m = dev_eval.realized_vol_1m.to_numpy()
    outcome = dev_eval.outcome_yes.to_numpy()
    groups = dev_eval.group_key.to_numpy()
    asset = dev_eval.asset.to_numpy()

    channels: dict[str, np.ndarray] = {}
    ses: dict[str, np.ndarray] = {}

    anchor = gaussian_terminal(buffer, sigma_rem, spot=spot, reference=reference,
                               name="normal_z_anchor")
    channels["gaussian"] = anchor.p_yes
    print(f"  gaussian (Normal-Z anchor)      n={len(anchor.p_yes)}")

    t_est = student_t_terminal(buffer, sigma_rem, nu=nu, spot=spot)
    channels["student_t"] = t_est.p_yes
    print(f"  student_t (nu={nu:.2f})              n={len(t_est.p_yes)}")

    emp_model = EmpiricalTerminalModel().fit(
        standardized_terminal_returns(
            fit_block.spot.to_numpy(), fit_block.terminal_price.to_numpy(),
            fit_block.sigma_remaining_return.to_numpy()),
        fit_block.seconds_remaining.to_numpy())
    emp = emp_model.predict(buffer, sigma_rem, seconds)
    channels["empirical"] = emp.p_yes
    ses["empirical"] = emp.standard_error
    print(f"  empirical conditional            n={len(emp.p_yes)}")

    boot_model = EmpiricalBootstrapModel(seed=SEED).fit(
        standardized_returns=standardized_terminal_returns(
            fit_block.spot.to_numpy(), fit_block.terminal_price.to_numpy(),
            fit_block.sigma_remaining_return.to_numpy()),
        seconds_remaining=fit_block.seconds_remaining.to_numpy(),
        sigma_1m=fit_block.realized_vol_1m.to_numpy(),
        asset=fit_block.asset.to_numpy())
    boot, boot_diag = boot_model.predict(
        buffer_pct=buffer, sigma_remaining=sigma_rem, seconds_remaining=seconds,
        sigma_1m=sigma_1m, asset=asset)
    channels["bootstrap"] = boot.p_yes
    ses["bootstrap"] = boot.standard_error
    print(f"  empirical bootstrap              n={len(boot.p_yes)}"
          f"  insufficient={pct(boot_diag.insufficient_fraction,2)}")
    print(f"    conditioning levels used: {boot_diag.level_counts}")

    # Monte Carlo on a stratified subsample (path simulation is the expensive
    # channel; the sample size is reported rather than hidden).
    n_eval = len(dev_eval)
    mc_n = min(args.mc_sample, n_eval)
    mc_rows = np.sort(rng.choice(n_eval, size=mc_n, replace=False))
    innovation_pool = np.diff(np.log(one_min["close"].to_numpy()))
    mc_engine = MonteCarloEngine(
        MonteCarloConfig(n_paths=args.mc_paths, seed=SEED, innovation="empirical"),
        innovation_pool=innovation_pool)
    mc = mc_engine.run(spot=spot[mc_rows], reference=reference[mc_rows],
                       seconds_remaining=seconds[mc_rows], sigma_1m=sigma_1m[mc_rows])
    mc_full = np.full(n_eval, np.nan)
    mc_full[mc_rows] = mc.p_yes
    channels["monte_carlo"] = mc_full
    mc_se_full = np.full(n_eval, np.nan)
    mc_se_full[mc_rows] = mc.standard_error
    ses["monte_carlo"] = mc_se_full
    print(f"  monte carlo                      n={mc_n} of {n_eval}"
          f"  ({args.mc_paths} paths, empirical innovations)")

    # Distribution/path diagnostics are the reason to retain Monte Carlo;
    # reproducing Phi(z) alone would not justify simulation expense.
    mc_terminal = {
        "n": int(mc_n), "paths": int(args.mc_paths),
        "probability_standard_error": quantiles(mc.standard_error, (0.05, 0.50, 0.95, 1.0)),
        "p_cross_reference": quantiles(mc.p_cross_reference),
        "gaussian_p_cross_reference": quantiles(anchor.p_cross_reference[mc_rows]),
        "crossing_difference_mc_minus_gaussian": quantiles(
            mc.p_cross_reference - anchor.p_cross_reference[mc_rows]
        ),
        "q05_terminal_return": quantiles(mc.quantile_05 / spot[mc_rows] - 1.0),
        "q50_terminal_return": quantiles(mc.quantile_50 / spot[mc_rows] - 1.0),
        "q95_terminal_return": quantiles(mc.quantile_95 / spot[mc_rows] - 1.0),
        "expected_terminal_buffer": quantiles(mc.expected_terminal_buffer),
    }
    print("\n  MONTE CARLO PATH / TAIL DIAGNOSTICS")
    print(f"    probability SE median={pct(mc_terminal['probability_standard_error']['q50'],3)}"
          f"  95th={pct(mc_terminal['probability_standard_error']['q95'],3)}"
          f"  max={pct(mc_terminal['probability_standard_error']['q100'],3)}")
    print(f"    crossing p median: MC={pct(mc_terminal['p_cross_reference']['q50'],2)}"
          f"  Gaussian={pct(mc_terminal['gaussian_p_cross_reference']['q50'],2)}")
    print(f"    MC-Gaussian crossing difference median="
          f"{pct(mc_terminal['crossing_difference_mc_minus_gaussian']['q50'],2)}")
    print("\n    terminal-return tail table (distribution across evaluated states)")
    print(f"    {'quantity':<24}{'5th':>11}{'median':>11}{'95th':>11}")
    print("    " + "-" * 57)
    for label, key in (("path 5% quantile", "q05_terminal_return"),
                       ("path median", "q50_terminal_return"),
                       ("path 95% quantile", "q95_terminal_return"),
                       ("expected terminal buffer", "expected_terminal_buffer")):
        row = mc_terminal[key]
        print(f"    {label:<24}{pct(row['q05'],3):>11}{pct(row['q50'],3):>11}"
              f"{pct(row['q95'],3):>11}")
    summary["monte_carlo"] = mc_terminal

    student_terminal = {
        "nu": float(nu), "n": int(len(t_est.p_yes)),
        "q05_terminal_return": quantiles(t_est.quantile_05 / spot - 1.0),
        "q50_terminal_return": quantiles(t_est.quantile_50 / spot - 1.0),
        "q95_terminal_return": quantiles(t_est.quantile_95 / spot - 1.0),
    }
    print("\n  STUDENT-T TERMINAL QUANTILES")
    for label, key in (("5% terminal quantile", "q05_terminal_return"),
                       ("median terminal", "q50_terminal_return"),
                       ("95% terminal quantile", "q95_terminal_return")):
        row = student_terminal[key]
        print(f"    {label:<24} state median={pct(row['q50'],3)}"
              f"  state 5-95%=[{pct(row['q05'],3)}, {pct(row['q95'],3)}]")
    summary["student_t_terminal"] = student_terminal

    # ------------------------------------------------- model comparison
    print("\n[4/9] CHALLENGERS vs NORMAL-Z (walk-forward evaluation rows)\n")
    print(f"  {'channel':<16}{'n':>8}{'brier':>9}{'logloss':>10}{'slope':>8}"
          f"{'ECE':>8}{'accuracy':>10}{'clustered 95% CI':>22}")
    print("  " + "-" * 91)
    model_rows = {}
    for name, p in channels.items():
        finite = np.isfinite(p)
        if finite.sum() < 100:
            continue
        rep = assess(p[finite], outcome[finite], name)
        acc = clustered_accuracy(
            np.where(p[finite] >= 0.5, outcome[finite] == 1, outcome[finite] == 0).astype(float),
            groups[finite], n_boot=args.boot, seed=SEED)
        model_rows[name] = {**rep.as_row(), "accuracy": acc.point,
                            "ci_low": acc.low, "ci_high": acc.high,
                            "n": int(finite.sum())}
        print(f"  {name:<16}{int(finite.sum()):>8}{f(rep.brier):>9}{f(rep.logloss):>10}"
              f"{f(rep.slope,3):>8}{f(rep.ece,4):>8}{pct(acc.point,2):>10}"
              f"{f'[{pct(acc.low,2)}, {pct(acc.high,2)}]':>22}")

    print(f"\n  {'challenger':<16}{'dBrier vs Normal-Z':>22}{'95% CI':>26}{'verdict':>22}")
    print("  " + "-" * 86)
    comparisons = {}
    for name, p in channels.items():
        if name == "gaussian":
            continue
        both = np.isfinite(p) & np.isfinite(channels["gaussian"])
        if both.sum() < 100:
            continue
        pt, lo, hi = paired_bootstrap_difference(
            p[both], channels["gaussian"][both], outcome[both], groups[both],
            "brier", n_boot=args.boot, seed=SEED)
        verdict = ("BEATS Normal-Z" if np.isfinite(hi) and hi < 0
                   else "WORSE" if np.isfinite(lo) and lo > 0
                   else "NOT DISTINGUISHABLE")
        comparisons[name] = {"delta_brier": pt, "ci": [lo, hi], "verdict": verdict,
                             "n": int(both.sum())}
        print(f"  {name:<16}{f(pt,5):>22}{f'[{f(lo,5)}, {f(hi,5)}]':>26}{verdict:>22}")
    print("\n  Negative dBrier = better than Normal-Z.")
    summary["models"] = model_rows
    summary["vs_normal_z"] = comparisons

    # ------------------------------------------------- sensitivities
    print("\n[5/9] DIGITAL SENSITIVITY DIAGNOSTICS (NOT directional alpha)\n")
    sens = digital_sensitivities(spot=spot, reference=reference,
                                 seconds_remaining=seconds, sigma_1m=sigma_1m)
    scaled = scaled_sensitivities(sens, spot, sigma_1m)
    print(f"  {'bucket':<12}{'mean d2':>10}{'|d2|':>10}{'|delta|/1%':>13}{'|gamma|/1%^2':>15}"
          f"{'|vega|/10%vol':>15}{'|theta|/30s':>13}")
    print("  " + "-" * 88)
    buckets = bucket_by_entry_time(seconds)
    sens_rows = {}
    for name, _, _ in ENTRY_TIME_BUCKETS:
        m = buckets == name
        if m.sum() == 0:
            continue
        row = {
            "n": int(m.sum()),
            "mean_d2": float(np.mean(sens.d2[m])),
            "mean_abs_d2": float(np.mean(np.abs(sens.d2[m]))),
            "abs_delta_per_pct": float(np.mean(np.abs(scaled["delta_per_pct"][m]))),
            "abs_gamma_per_pct2": float(np.mean(np.abs(scaled["gamma_per_pct2"][m]))),
            "abs_vega_per_10pct": float(np.mean(np.abs(scaled["vega_per_10pct_vol"][m]))),
            "abs_theta_per_30s": float(np.mean(np.abs(scaled["theta_per_30s"][m]))),
        }
        sens_rows[name] = row
        print(f"  {name:<12}{row['mean_d2']:>10.3f}{row['mean_abs_d2']:>10.3f}"
              f"{row['abs_delta_per_pct']:>13.4f}"
              f"{row['abs_gamma_per_pct2']:>15.4f}{row['abs_vega_per_10pct']:>15.4f}"
              f"{row['abs_theta_per_30s']:>13.4f}")
    summary["sensitivities"] = sens_rows

    # ------------------------------------------------- agreement
    print("\n[6/9] MODEL AGREEMENT / DISAGREEMENT\n")
    agreement = combine_channels(channels, anchor="gaussian")
    lower_bound = conservative_lower_bound(channels, anchor="gaussian",
                                           standard_errors=ses)
    anchor_conf = np.maximum(channels["gaussian"], 1.0 - channels["gaussian"])
    print(f"  mean channels per row : {f(float(np.nanmean(agreement.n_channels)),2)}")
    print(f"  mean disagreement     : {f(float(np.nanmean(agreement.disagreement)),4)}")
    print(f"  median disagreement   : {f(float(np.nanmedian(agreement.disagreement)),4)}")
    print(f"  mean anchor confidence: {pct(float(np.mean(anchor_conf)))}")
    print(f"  mean lower bound      : {pct(float(np.nanmean(lower_bound)))}")
    print(f"  mean bound shortfall  : "
          f"{pct(float(np.nanmean(anchor_conf - lower_bound)))} below the point estimate")

    study = disagreement_error_study(
        anchor_p=channels["gaussian"], outcome_yes=outcome,
        disagreement=agreement.disagreement, groups=groups, confidence_floor=0.80)
    print(f"\n  DOES DISAGREEMENT PREDICT ERRORS?  (anchor confidence >= 0.80)")
    if study["status"] != "OK":
        print(f"    {study['status']}")
    else:
        print(f"    n={study['n']}  windows={study['n_windows']}  "
              f"accuracy={pct(study['overall_accuracy'])}")
        print(f"    mean disagreement | winners = {f(study['mean_disagreement_winners'],4)}")
        print(f"    mean disagreement | losers  = {f(study['mean_disagreement_losers'],4)}")
        print(f"    gap (losers - winners)      = {f(study['disagreement_gap'],4)}")
        print(f"\n    {'quantile':>9}{'range':>22}{'n':>8}{'accuracy':>10}{'clustered 95% CI':>22}")
        print("    " + "-" * 69)
        for b in study["buckets"]:
            disagreement_range = (
                f"[{f(b['disagreement_low'],4)}, {f(b['disagreement_high'],4)}]"
            )
            accuracy_ci = f"[{pct(b['ci_low'],2)}, {pct(b['ci_high'],2)}]"
            print(f"    {b['quantile']:>9}"
                  f"{disagreement_range:>22}"
                  f"{b['n']:>8}{pct(b['accuracy'],2):>10}"
                  f"{accuracy_ci:>22}")
        print(f"\n    SEPARATES (non-overlapping CIs): {study['separates']}")
        if study.get("accuracy_drop_low_to_high") is not None:
            print(f"    accuracy drop low->high disagreement: "
                  f"{study['accuracy_drop_low_to_high']*100:+.2f} pp")
    summary["agreement"] = {
        "mean_disagreement": float(np.nanmean(agreement.disagreement)),
        "mean_lower_bound": float(np.nanmean(lower_bound)),
        "mean_anchor_confidence": float(np.mean(anchor_conf)),
        "study": study,
    }

    # ------------------------------------------------- fragility
    print("\n[7/9] FRAGILITY\n")
    vov = volatility_of_volatility(dev_eval.rv_5m.to_numpy(), dev_eval.rv_30m.to_numpy())
    frag = compute_fragility(
        gamma_per_pct2=scaled["gamma_per_pct2"],
        vega_per_10pct_vol=scaled["vega_per_10pct_vol"],
        theta_per_30s=scaled["theta_per_30s"],
        abs_z=np.abs(dev_eval.normal_z.to_numpy()),
        seconds_remaining=seconds,
        crossings=dev_eval.crossings.to_numpy() if "crossings" in dev_eval else None,
        vol_of_vol=vov,
        disagreement=agreement.disagreement,
    )
    print(f"  score mean={f(float(frag.score.mean()),2)}  "
          f"median={f(float(np.median(frag.score)),2)}")
    print(f"  {'band':<10}{'n':>9}{'share':>9}{'accuracy':>10}{'clustered 95% CI':>22}{'brier':>9}")
    print("  " + "-" * 69)
    frag_rows = {}
    won_all = np.where(channels["gaussian"] >= 0.5, outcome == 1, outcome == 0).astype(float)
    for band in BANDS:
        m = frag.band == band
        if m.sum() < 30:
            continue
        acc = clustered_accuracy(won_all[m], groups[m], n_boot=args.boot, seed=SEED)
        rep = assess(channels["gaussian"][m], outcome[m], band)
        frag_rows[band] = {"n": int(m.sum()), "share": float(m.mean()),
                           "accuracy": acc.point, "ci": [acc.low, acc.high],
                           "brier": rep.brier}
        print(f"  {band:<10}{int(m.sum()):>9}{pct(float(m.mean()),1):>9}"
              f"{pct(acc.point,2):>10}{f'[{pct(acc.low,2)}, {pct(acc.high,2)}]':>22}"
              f"{f(rep.brier):>9}")
    summary["fragility"] = {"bands": frag_rows, "summary": frag.summary(),
                            "weights": frag.weights}

    print("\n  CONSERVATIVE PROBABILITY BOUNDS BY SLICE")
    print(f"  {'slice':<24}{'n':>8}{'point conf':>13}{'lower bound':>13}{'shortfall':>12}")
    print("  " + "-" * 70)
    bound_rows = {}
    bound_slices = []
    for threshold in (0.80, 0.85, 0.90, 0.95):
        bound_slices.append((f"confidence>={threshold:.2f}", anchor_conf >= threshold))
    for band in BANDS:
        bound_slices.append((f"fragility={band}", frag.band == band))
    for label, mask in bound_slices:
        finite = mask & np.isfinite(lower_bound)
        if finite.sum() < 30:
            continue
        point = float(np.mean(anchor_conf[finite]))
        lower = float(np.mean(lower_bound[finite]))
        row = {"n": int(finite.sum()), "point_confidence": point,
               "mean_lower_bound": lower, "mean_shortfall": point - lower,
               "lower_bound_quantiles": quantiles(lower_bound[finite])}
        bound_rows[label] = row
        print(f"  {label:<24}{row['n']:>8}{pct(point,2):>13}{pct(lower,2):>13}"
              f"{pct(point-lower,2):>12}")
    summary["conservative_bounds"] = bound_rows

    # --------------------------------- selective prediction + fragility
    print("\n[8/9] SELECTIVE PREDICTION: PROBABILITY ALONE vs PROBABILITY + FRAGILITY\n")
    print(f"  {'threshold':>10}{'filter':<22}{'coverage':>10}{'trades':>9}"
          f"{'accuracy':>10}{'clustered 95% CI':>22}")
    print("  " + "-" * 83)
    selective_rows = []
    for threshold in (0.80, 0.85, 0.90, 0.95):
        base_mask = anchor_conf >= threshold
        variants = [
            ("probability only", base_mask),
            ("+ fragility<=HIGH", base_mask & (frag.band != "EXTREME")),
            ("+ fragility<=MEDIUM", base_mask & np.isin(frag.band, ["LOW", "MEDIUM"])),
            ("+ fragility=LOW", base_mask & (frag.band == "LOW")),
        ]
        for label, mask in variants:
            if mask.sum() < 50:
                print(f"  {threshold:>10.2f}{label:<22}{'-':>10}{int(mask.sum()):>9}"
                      f"{'too few':>10}{'':>22}")
                continue
            acc = clustered_accuracy(won_all[mask], groups[mask], n_boot=args.boot, seed=SEED)
            rep = assess(channels["gaussian"][mask], outcome[mask], label)
            selective_rows.append({
                "threshold": threshold, "filter": label,
                "coverage": float(mask.mean()), "n": int(mask.sum()),
                "accuracy": acc.point, "ci": [acc.low, acc.high],
                "brier": rep.brier, "logloss": rep.logloss,
                "economic_profitability": "NOT EVALUABLE - no historical contract prices",
            })
            print(f"  {threshold:>10.2f}{label:<22}{pct(float(mask.mean()),1):>10}"
                  f"{int(mask.sum()):>9}{pct(acc.point,2):>10}"
                  f"{f'[{pct(acc.low,2)}, {pct(acc.high,2)}]':>22}")
    summary["selective"] = selective_rows

    # ------------------------------------------------- volatility study
    print("\n[9/9] VOLATILITY ESTIMATOR COMPARISON\n")
    print("  Judged on Brier / log loss / calibration, NOT accuracy:")
    print("  scaling sigma leaves the sign of z unchanged, so accuracy is")
    print("  near-invariant while calibration is not.\n")
    print(f"  {'estimator':<18}{'coverage':>10}{'brier':>9}{'logloss':>10}"
          f"{'slope':>8}{'ECE':>8}{'accuracy':>10}{'real/pred':>11}")
    print("  " + "-" * 84)

    ext_cache = {a: precompute_extended(fr) for a, fr in frames.items()}
    base_cache = {a: precompute_indicators(fr) for a, fr in frames.items()}
    vol_rows = {}
    vol_predictions = {}
    realised = (dev_eval.terminal_price.to_numpy() - spot) / spot

    for estimator in build_estimators():
        sigma_est = np.full(len(dev_eval), np.nan)
        for a in frames:
            series = estimator.build(frames[a], base_cache[a], ext_cache[a])
            lookup = pd.Series(np.asarray(series, dtype="float64"), index=frames[a].index)
            m = asset == a
            if not m.any():
                continue
            stamps = pd.DatetimeIndex(dev_eval.scan_utc.to_numpy()[m]) - pd.Timedelta(minutes=1)
            sigma_est[m] = lookup.reindex(stamps, method="ffill").to_numpy()

        ok = np.isfinite(sigma_est) & (sigma_est > 0)
        if ok.sum() < 1000:
            print(f"  {estimator.name:<18}{'insufficient':>10}")
            continue
        s_rem = sigma_remaining_from(sigma_est[ok], seconds[ok])
        p = gaussian_terminal(buffer[ok], s_rem).p_yes
        rep = assess(p, outcome[ok], estimator.name)
        correct = np.where(p >= 0.5, outcome[ok] == 1, outcome[ok] == 0).astype(float)
        acc_interval = clustered_accuracy(correct, groups[ok], n_boot=args.boot, seed=SEED)
        brier_interval = clustered_metric(
            p, outcome[ok], groups[ok], "brier", n_boot=args.boot, seed=SEED)
        logloss_interval = clustered_metric(
            p, outcome[ok], groups[ok], "logloss", n_boot=args.boot, seed=SEED)
        acc = acc_interval.point
        rop = float(np.std(realised[ok], ddof=1) / np.mean(s_rem))
        full_prediction = np.full(len(dev_eval), np.nan)
        full_prediction[ok] = p
        vol_predictions[estimator.name] = full_prediction
        vol_rows[estimator.name] = {
            "description": estimator.description, "n": int(ok.sum()),
            "coverage": float(ok.mean()), "brier": rep.brier, "logloss": rep.logloss,
            "slope": rep.slope, "intercept": rep.intercept, "ece": rep.ece,
            "accuracy": acc, "realised_over_predicted": rop,
            "brier_ci": [brier_interval.low, brier_interval.high],
            "logloss_ci": [logloss_interval.low, logloss_interval.high],
            "accuracy_ci": [acc_interval.low, acc_interval.high],
            "n_windows": int(len(np.unique(groups[ok]))),
            "mean_sigma_1m": float(np.mean(sigma_est[ok])),
        }
        print(f"  {estimator.name:<18}{pct(float(ok.mean()),1):>10}{f(rep.brier):>9}"
              f"{f(rep.logloss):>10}{f(rep.slope,3):>8}{f(rep.ece,4):>8}"
              f"{pct(acc,2):>10}{f(rop,3):>11}")
    summary["volatility"] = vol_rows

    print("\n  WINDOW-CLUSTERED INTERVALS AND PAIRED BRIER vs VALIDATED")
    print(f"  {'estimator':<18}{'Brier 95% CI':>25}{'accuracy 95% CI':>25}"
          f"{'dBrier 95% CI':>27}{'verdict':>20}")
    print("  " + "-" * 115)
    vol_comparisons = {}
    validated_p = vol_predictions.get("validated")
    for name, p in vol_predictions.items():
        row = vol_rows[name]
        if name == "validated":
            delta, lo, hi, verdict = 0.0, 0.0, 0.0, "ANCHOR ESTIMATOR"
        else:
            both = np.isfinite(p) & np.isfinite(validated_p)
            delta, lo, hi = paired_bootstrap_difference(
                p[both], validated_p[both], outcome[both], groups[both],
                "brier", n_boot=args.boot, seed=SEED)
            verdict = ("BEATS validated" if np.isfinite(hi) and hi < 0
                       else "WORSE" if np.isfinite(lo) and lo > 0
                       else "NOT DISTINGUISHABLE")
        vol_comparisons[name] = {"delta_brier": delta, "ci": [lo, hi],
                                 "verdict": verdict, "n": row["n"]}
        bci = f"[{f(row['brier_ci'][0],5)}, {f(row['brier_ci'][1],5)}]"
        aci = f"[{pct(row['accuracy_ci'][0],2)}, {pct(row['accuracy_ci'][1],2)}]"
        dci = f"{f(delta,5)} [{f(lo,5)}, {f(hi,5)}]"
        print(f"  {name:<18}{bci:>25}{aci:>25}{dci:>27}{verdict:>20}")
    summary["volatility_vs_validated"] = vol_comparisons

    # ------------------------------------------ cross-asset dependence
    print("\n[CROSS-ASSET] SAME-WINDOW DEPENDENCE DIAGNOSTICS\n")
    contract_rows = dev_eval[["group_key", "asset", "reference", "terminal_price"]].drop_duplicates(
        ["group_key", "asset"]
    ).copy()
    contract_rows["terminal_return"] = (
        contract_rows["terminal_price"] / contract_rows["reference"] - 1.0
    )
    return_panel = contract_rows.pivot(index="group_key", columns="asset", values="terminal_return")
    corr = return_panel.corr(min_periods=30)
    corr_values = corr.to_numpy(dtype="float64")
    off_diag = corr_values[np.triu_indices_from(corr_values, k=1)]
    complete_corr = np.nan_to_num(corr_values, nan=0.0)
    np.fill_diagonal(complete_corr, 1.0)
    eigenvalues = np.maximum(np.linalg.eigvalsh(complete_corr), 0.0)
    effective_assets = float(eigenvalues.sum() ** 2 / np.sum(eigenvalues ** 2))
    direction_panel = np.sign(return_panel)
    direction_agreement = float(
        direction_panel.apply(
            lambda row: max((row > 0).mean(), (row < 0).mean()), axis=1
        ).mean()
    )
    cross_asset = {
        "n_windows": int(len(return_panel)),
        "assets": list(return_panel.columns),
        "correlation_matrix": corr.to_dict(),
        "mean_pairwise_correlation": float(np.nanmean(off_diag)),
        "median_pairwise_correlation": float(np.nanmedian(off_diag)),
        "effective_independent_assets": effective_assets,
        "mean_same_direction_share": direction_agreement,
        "interpretation": (
            "Same-window assets are materially dependent; rows are not independent "
            "bets and uncertainty must remain window-clustered."
        ),
    }
    print(f"  windows={cross_asset['n_windows']}  assets={len(cross_asset['assets'])}")
    print(f"  mean pairwise terminal-return correlation="
          f"{f(cross_asset['mean_pairwise_correlation'],3)}")
    print(f"  effective independent assets of {len(cross_asset['assets'])}="
          f"{f(effective_assets,2)}")
    print(f"  mean share resolving in the same direction={pct(direction_agreement,1)}")
    print("\n  terminal-return correlation matrix:")
    print(corr.round(3).to_string())
    summary["cross_asset_dependence"] = cross_asset

    robust_winners = [name for name, row in comparisons.items()
                      if row["verdict"] == "BEATS Normal-Z"]
    if robust_winners:
        # This is deliberately descriptive only. Any replacement still needs
        # user review because Phase 5 does not silently promote a model.
        conclusion = ("At least one challenger cleared the paired clustered Brier interval: "
                      + ", ".join(robust_winners)
                      + ". Normal-Z remains the anchor pending explicit review.")
    else:
        conclusion = ("No challenger robustly beat Normal-Z on out-of-sample Brier score. "
                      "Normal-Z remains the probability anchor.")
    summary["conclusion"] = conclusion
    summary["survival_recommendations"] = [
        "Normal-Z: SURVIVES as the probability anchor; it is not altered by fragility.",
        "Student-t: retain only as a heavy-tail diagnostic unless its paired clustered Brier interval beats Normal-Z.",
        "Empirical conditional distribution and empirical bootstrap: retain as diagnostic challengers, not production replacements without robust OOS improvement.",
        "Monte Carlo: retain for terminal quantiles, sampling error, and path-dependent crossing diagnostics; not as directional alpha.",
        "Digital sensitivities (d2/delta/gamma/vega/theta): retain as diagnostics only.",
        "Fragility and disagreement: retain as abstention research diagnostics; do not shade calibrated probability with either score.",
        "Conservative lower bounds: retain as displayed uncertainty diagnostics where sampling error/model dispersion is defensible.",
        "Validated volatility estimator: retain unless a challenger beats it under paired window-clustered Brier comparison.",
        "Same-window grouping and cross-asset dependence adjustment: mandatory for all later uncertainty estimates.",
    ]
    summary["detailed_limitations"] = [
        "PROXY SETTLEMENT REFERENCE: labels use the first qualifying underlying bar, not a verified venue settlement reference.",
        "NO HISTORICAL WEBULL CONTRACT QUOTES: market-implied probability, spread, and execution cost cannot be reconstructed.",
        "CLASSIFICATION RESEARCH ONLY.",
        "NOT A PROFITABILITY BACKTEST: no EV, P&L, break-even, fee, slippage, or return claim is supported.",
        "LIMITED RECENT HISTORICAL REGIME: the sample does not establish robustness across bull, bear, shock, or illiquid regimes.",
        "The untouched chronological holdout is not used in Phase 5 tuning or model selection.",
        "Multiple scan rows share one contract outcome; effective sample size is far below row count.",
        "BTC/ETH/SOL/ADA/XRP within the same 15-minute window are strongly dependent and cannot be counted as independent bets.",
        "Yahoo one-minute bars are coarse and may differ from live or venue-index observations.",
        "Monte Carlo standard error measures finite-path sampling error only, not model misspecification.",
        "Gaussian crossing probability relies on a driftless continuous-path approximation; empirical 15-second MC crossing is grid-dependent.",
        "Empirical/bootstrap channels assume the limited recent training regime is informative about evaluation states.",
        "Fragility weights are declared diagnostics, not outcome-fitted probabilities; selective improvements may partly reflect mechanically easier late-window states.",
        "NO TRADE is an abstention, not an incorrect prediction.",
    ]

    path = out_dir / "mantis_v4_phase5_summary.json"
    path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    report_path = PROJECT_ROOT / "research" / "PHASE5_RESULTS.md"
    write_markdown_report(report_path, summary)
    print(f"\n  summary written: {path}")
    print(f"  report written : {report_path}")
    print(f"  CONCLUSION: {conclusion}")
    print(f"  holdout seal intact: {plan.seal.intact} (holdout NOT used in Phase 5 tuning)")
    print()
    banner()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
