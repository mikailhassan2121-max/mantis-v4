#!/usr/bin/env python3
# ================================================================
# MANTIS V4 — PHASE 3 BACKTEST RUNNER
#
# Builds the historical dataset, runs the baseline comparisons, and
# writes the diagnostics reports.
#
# This is RESEARCH tooling. It trains no model, tunes no threshold,
# and makes no profitability claim. Every reference it reconstructs is
# an UNVERIFIED PROXY, and every result is PRELIMINARY.
#
# V3 (mantis_15m_resolution_v3.py) is never touched.
# ================================================================

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mantis_v4.clock import load_timezone                            # noqa: E402
from mantis_v4.config import PROJECT_ROOT, MantisConfig              # noqa: E402
from mantis_v4.contracts import SettlementRule                       # noqa: E402
from mantis_v4.backtest import (                                     # noqa: E402
    EventDrivenBacktester,
    YFinanceHistoryProvider,
    analyse_bars,
    analyse_contracts,
    analyse_cross_asset,
    build_report,
    default_strategies,
    load_universe,
    purged_walk_forward,
    replay_to_row,
    spans_from_replays,
    write_dataset,
    write_schema_documentation,
)
from mantis_v4.backtest.splits import summarise as summarise_splits  # noqa: E402

SEPARATOR = "=" * 78


def fmt(value, digits: int = 4, dash: str = "N/A") -> str:
    if value is None:
        return dash
    if isinstance(value, float):
        if value != value:
            return dash
        return f"{value:.{digits}f}"
    return str(value)


def fmt_pct(value, digits: int = 2, dash: str = "N/A") -> str:
    if value is None or (isinstance(value, float) and value != value):
        return dash
    return f"{value * 100:.{digits}f}%"


def print_metrics_table(title: str, metrics_by_label: dict) -> None:
    if not metrics_by_label:
        return
    print(f"\n  {title}")
    print(f"  {'slice':<16}{'obs':>7}{'traded':>8}{'cover':>9}{'acc':>9}"
          f"{'95% CI':>18}{'base YES':>10}{'Brier':>9}")
    print("  " + "-" * 86)
    for label, m in metrics_by_label.items():
        low, high = m.accuracy_ci
        ci = "N/A" if low is None else f"[{low * 100:.1f},{high * 100:.1f}]"
        print(
            f"  {label:<16}{m.contracts_observed:>7}{m.contracts_traded:>8}"
            f"{fmt_pct(m.coverage, 1):>9}{fmt_pct(m.accuracy, 1):>9}"
            f"{ci:>18}{fmt_pct(m.base_rate_yes, 1):>10}{fmt(m.brier):>9}"
        )


def run(args) -> int:
    config = MantisConfig.load()
    timezone = load_timezone(config.contract_timezone)
    assets = config.active_assets

    out_dir = PROJECT_ROOT / "data"
    research_dir = PROJECT_ROOT / "research"
    out_dir.mkdir(parents=True, exist_ok=True)
    research_dir.mkdir(parents=True, exist_ok=True)

    print(SEPARATOR)
    print("  MANTIS V4 - PHASE 3 BACKTEST")
    print(SEPARATOR)
    print(f"  assets          : {', '.join(assets)}")
    print(f"  history days    : {args.days}")
    print(f"  scan interval   : {args.scan_interval}s")
    print(f"  timezone        : {config.contract_timezone}")
    print(f"  settlement      : PROXY_TERMINAL_ABOVE_REFERENCE (UNVERIFIED)")
    print(SEPARATOR)

    # ---------------------------------------------------------------- load
    print("\n[1/6] LOADING HISTORY (yfinance bootstrap; ~28 day ceiling)\n")
    provider = YFinanceHistoryProvider(
        out_dir / "history",
        timeout_seconds=config.network_timeout_seconds,
        use_cache=not args.no_cache,
    )
    frames, load_reports = load_universe(
        provider, assets, args.days,
        min_bars=args.min_bars, min_completeness=args.min_completeness,
    )
    for report in load_reports:
        print(f"  {report.summary()}")

    if not frames:
        print("\n  NO ASSET HAD ADEQUATE DATA. Nothing to backtest.")
        return 1

    skipped = [r.asset for r in load_reports if r.skipped]
    if skipped:
        print(f"\n  SKIPPED (explicitly, not gap-filled): {', '.join(skipped)}")

    # -------------------------------------------------------- bar quality
    print("\n[2/6] BAR-LEVEL DATA QUALITY\n")
    bar_reports = {a: analyse_bars(a, f) for a, f in frames.items()}
    print(f"  {'asset':<10}{'bars':>8}{'span_d':>9}{'complete':>10}{'dupes':>7}"
          f"{'gaps':>6}{'maxgap_m':>10}{'staleRun':>9}{'tz':>6}")
    print("  " + "-" * 76)
    for asset, r in sorted(bar_reports.items()):
        print(
            f"  {asset:<10}{r.bars:>8}{r.span_days:>9.2f}"
            f"{r.completeness * 100:>9.2f}%{r.duplicate_timestamps:>7}"
            f"{r.gap_count:>6}{r.max_gap_minutes:>10.1f}{r.longest_stale_run:>9}"
            f"{'OK' if r.timezone_consistent else 'BAD':>6}"
        )

    # ------------------------------------------------------------- replay
    print("\n[3/6] REPLAYING CONTRACTS (event-driven, leakage-safe)\n")
    backtester = EventDrivenBacktester(
        frames, timezone,
        window_minutes=config.contract_window_minutes,
        scan_interval_seconds=args.scan_interval,
        settlement_rule=SettlementRule.PROXY_TERMINAL_ABOVE_REFERENCE,
        paranoid=args.paranoid,
    )

    window_counts = {a: len(backtester.windows_for(a)) for a in frames}
    total_windows = sum(window_counts.values())
    print(f"  contract-asset windows available: {total_windows}")
    for asset, count in sorted(window_counts.items()):
        print(f"    {asset:<10} {count:>6}")

    strategies = default_strategies()
    all_rows = []
    reports = {}
    contract_quality = None

    for strategy in strategies:
        if not strategy.evaluable:
            print(f"\n  {strategy.name}: NOT EVALUABLE")
            print(f"    reason: {strategy.not_evaluable_reason}")
            report = build_report(strategy.name, [])
            report.evaluable = False
            report.not_evaluable_reason = strategy.not_evaluable_reason
            reports[strategy.name] = report
            continue

        print(f"\n  running {strategy.name} ...", end="", flush=True)
        replays = backtester.replay_all(strategy, limit=args.limit)
        print(f" {len(replays)} contracts")

        if contract_quality is None:
            contract_quality = analyse_contracts(replays)
            cross_asset = analyse_cross_asset(frames, replays)
            reference_replays = replays

        reports[strategy.name] = build_report(strategy.name, replays)
        if hasattr(strategy, "min_buffer_z_ever_binding"):
            reports[strategy.name].min_buffer_z_binding_count = (
                strategy.min_buffer_z_ever_binding
            )

        all_rows.extend(
            replay_to_row(r, SettlementRule.PROXY_TERMINAL_ABOVE_REFERENCE.value)
            for r in replays
        )

    # -------------------------------------------------- contract quality
    print("\n[4/6] CONTRACT-LEVEL QUALITY\n")
    print(f"  {'asset':<10}{'total':>8}{'usable':>8}{'usable%':>9}"
          f"{'noRef':>7}{'noTerm':>8}{'YES':>7}{'NO':>7}{'baseYES':>9}")
    print("  " + "-" * 73)
    for asset, r in sorted((contract_quality or {}).items()):
        print(
            f"  {asset:<10}{r.total_contracts:>8}{r.usable_contracts:>8}"
            f"{r.usable_pct:>8.2f}%{r.no_start_reference:>7}{r.no_terminal_bar:>8}"
            f"{r.yes_outcomes:>7}{r.no_outcomes:>7}"
            f"{fmt_pct(r.base_rate_yes, 1):>9}"
        )

    # ---------------------------------------------------- cross-asset
    print("\n[5/6] CROSS-ASSET CORRELATION DIAGNOSTICS\n")
    print(f"  mean pairwise 1m return correlation : {fmt(cross_asset.mean_return_correlation)}")
    print(f"  mean pairwise outcome agreement     : {fmt_pct(cross_asset.mean_outcome_agreement, 1)}")
    print(f"  windows with all assets present     : {cross_asset.shared_windows}")
    print(f"  unanimous windows                   : {cross_asset.unanimous_windows}"
          f"  ({fmt_pct(cross_asset.unanimous_rate, 1)})")
    print(f"  expected if independent             : "
          f"{fmt_pct(cross_asset.expected_unanimous_rate_if_independent, 1)}")
    print(f"  effective independent assets/window : "
          f"{fmt(cross_asset.effective_sample_size_factor, 2)} of {len(cross_asset.assets)}")
    if cross_asset.unanimity_histogram:
        print("\n  YES-count distribution across simultaneous contracts:")
        for yes_count, n in cross_asset.unanimity_histogram.items():
            share = n / max(1, cross_asset.shared_windows)
            bar = "#" * int(share * 50)
            print(f"    {yes_count} YES : {n:>6}  {share * 100:>5.1f}%  {bar}")

    # --------------------------------------------------------- baselines
    print("\n[6/6] BASELINE RESULTS")
    print("\n  CLASSIFICATION ACCURACY ONLY.")
    print("  Economic profitability is NOT EVALUABLE: no historical contract prices exist.")
    print("  Do not infer profitability from accuracy.\n")

    for name, report in reports.items():
        print(SEPARATOR)
        print(f"  {name}")
        if not report.evaluable:
            print(f"    STATUS: NOT EVALUABLE\n    {report.not_evaluable_reason}")
            continue
        m = report.overall
        low, high = m.accuracy_ci
        print(f"    contracts observed : {m.contracts_observed}")
        print(f"    contracts traded   : {m.contracts_traded}")
        print(f"    coverage           : {fmt_pct(m.coverage, 2)}")
        print(f"    abstention rate    : {fmt_pct(m.abstention_rate, 2)}")
        print(f"    accuracy           : {fmt_pct(m.accuracy, 2)}")
        print(f"    95% Wilson CI      : "
              f"{'N/A' if low is None else f'[{low * 100:.2f}%, {high * 100:.2f}%]'}")
        print(f"    base rate YES      : {fmt_pct(m.base_rate_yes, 2)}")
        print(f"    Brier              : {fmt(m.brier)}"
              f"{'' if m.probabilities_available else '  (no probability output)'}")
        print(f"    log loss           : {fmt(m.logloss)}")
        print(f"    economic P&L       : NOT EVALUABLE")
        if report.min_buffer_z_binding_count is not None:
            print(f"    MIN_BUFFER_Z bound : {report.min_buffer_z_binding_count} times "
                  f"(audit B-3 predicts 0)")

        print_metrics_table("by asset", report.by_asset)
        print_metrics_table("by direction", report.by_direction)
        print_metrics_table("by entry-time bucket", report.by_entry_time)
        print_metrics_table("by volatility bucket", report.by_volatility)

    # ------------------------------------------------------------ write
    dataset_path = out_dir / "mantis_v4_historical_dataset.csv"
    write_report = write_dataset(all_rows, dataset_path)
    schema_path = write_schema_documentation(out_dir / "mantis_v4_dataset_schema.json")

    spans = spans_from_replays(reference_replays)
    splits = purged_walk_forward(spans, folds=5)

    print("\n" + SEPARATOR)
    print("  OUTPUTS")
    print(SEPARATOR)
    print(f"  dataset : {dataset_path}  ({write_report.rows} rows, "
          f"{write_report.usable_rows} usable, {write_report.entered_rows} entered)")
    print(f"  schema  : {schema_path}")
    print(f"\n  purged walk-forward preview (5 folds, 15-minute embargo):")
    for row in summarise_splits(splits):
        print(f"    fold {row['fold']}: train={row['train']:>6} test={row['test']:>6} "
              f"purged={row['purged']:>4} embargoed={row['embargoed']:>4}")

    summary_path = out_dir / "mantis_v4_phase3_summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "assets_loaded": sorted(frames),
                "assets_skipped": skipped,
                "history_days_requested": args.days,
                "scan_interval_seconds": args.scan_interval,
                "bar_quality": {a: r.as_row() for a, r in bar_reports.items()},
                "contract_quality": {
                    a: r.as_row() for a, r in (contract_quality or {}).items()
                },
                "cross_asset": cross_asset.as_dict(),
                "return_correlation": cross_asset.return_correlation,
                "outcome_agreement": cross_asset.outcome_agreement,
                "baselines": {
                    name: (
                        {"evaluable": False, "reason": rep.not_evaluable_reason}
                        if not rep.evaluable
                        else {
                            "evaluable": True,
                            "overall": rep.overall.as_row(),
                            "by_asset": {k: v.as_row() for k, v in rep.by_asset.items()},
                            "by_direction": {k: v.as_row() for k, v in rep.by_direction.items()},
                            "by_entry_time": {k: v.as_row() for k, v in rep.by_entry_time.items()},
                            "by_volatility": {k: v.as_row() for k, v in rep.by_volatility.items()},
                        }
                    )
                    for name, rep in reports.items()
                },
                "walk_forward_preview": summarise_splits(splits),
                "economic_profitability": "NOT EVALUABLE - no historical contract prices",
                "all_references": "PROXY / UNVERIFIED",
            },
            indent=2, default=str,
        ),
        encoding="utf-8",
    )
    print(f"  summary : {summary_path}")

    print("\n" + SEPARATOR)
    print("  All results are PRELIMINARY. Reference is an UNVERIFIED PROXY.")
    print("  Classification accuracy is NOT economic profitability.")
    print(SEPARATOR)
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mantis_v4_backtest",
        description="MANTIS V4 Phase 3: historical dataset + leakage-safe baselines",
    )
    parser.add_argument("--days", type=int, default=28,
                        help="days of 1-minute history (source ceiling ~28)")
    parser.add_argument("--scan-interval", type=int, default=15,
                        help="seconds between historical scans")
    parser.add_argument("--limit", type=int, default=None,
                        help="cap contracts per asset (smoke tests)")
    parser.add_argument("--min-bars", type=int, default=2000,
                        help="minimum bars before an asset is used")
    parser.add_argument("--min-completeness", type=float, default=0.80,
                        help="minimum bar completeness before an asset is used")
    parser.add_argument("--no-cache", action="store_true",
                        help="ignore the on-disk history cache")
    parser.add_argument("--paranoid", action="store_true",
                        help="self-verify every market view for look-ahead")
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
