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
    print(f"  {'bucket':<12}{'|delta|/1%':>13}{'|gamma|/1%^2':>15}"
          f"{'|vega|/10%vol':>15}{'|theta|/30s':>13}")
    print("  " + "-" * 68)
    buckets = bucket_by_entry_time(seconds)
    sens_rows = {}
    for name, _, _ in ENTRY_TIME_BUCKETS:
        m = buckets == name
        if m.sum() == 0:
            continue
        row = {
            "n": int(m.sum()),
            "abs_delta_per_pct": float(np.mean(np.abs(scaled["delta_per_pct"][m]))),
            "abs_gamma_per_pct2": float(np.mean(np.abs(scaled["gamma_per_pct2"][m]))),
            "abs_vega_per_10pct": float(np.mean(np.abs(scaled["vega_per_10pct_vol"][m]))),
            "abs_theta_per_30s": float(np.mean(np.abs(scaled["theta_per_30s"][m]))),
        }
        sens_rows[name] = row
        print(f"  {name:<12}{row['abs_delta_per_pct']:>13.4f}"
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
            print(f"    {b['quantile']:>9}"
                  f"{f'[{f(b[chr(100)+chr(105)+chr(115)+chr(97)+chr(103)+chr(114)+chr(101)+chr(101)+chr(109)+chr(101)+chr(110)+chr(116)+chr(95)+chr(108)+chr(111)+chr(119)],4)}, {f(b[\"disagreement_high\"],4)}]':>22}"
                  f"{b['n']:>8}{pct(b['accuracy'],2):>10}"
                  f"{f'[{pct(b[\"ci_low\"],2)}, {pct(b[\"ci_high\"],2)}]':>22}")
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
        acc = float(np.mean(np.where(p >= 0.5, outcome[ok] == 1, outcome[ok] == 0)))
        rop = float(np.std(realised[ok], ddof=1) / np.mean(s_rem))
        vol_rows[estimator.name] = {
            "description": estimator.description, "n": int(ok.sum()),
            "coverage": float(ok.mean()), "brier": rep.brier, "logloss": rep.logloss,
            "slope": rep.slope, "intercept": rep.intercept, "ece": rep.ece,
            "accuracy": acc, "realised_over_predicted": rop,
            "mean_sigma_1m": float(np.mean(sigma_est[ok])),
        }
        print(f"  {estimator.name:<18}{pct(float(ok.mean()),1):>10}{f(rep.brier):>9}"
              f"{f(rep.logloss):>10}{f(rep.slope,3):>8}{f(rep.ece,4):>8}"
              f"{pct(acc,2):>10}{f(rop,3):>11}")
    summary["volatility"] = vol_rows

    path = out_dir / "mantis_v4_phase5_summary.json"
    path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(f"\n  summary written: {path}")
    print(f"  holdout seal intact: {plan.seal.intact} (holdout NOT used in Phase 5 tuning)")
    print()
    banner()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
