#!/usr/bin/env python3
# ================================================================
# MANTIS // 15M RESOLUTION V4 — Market Analysis and Neuro-Tactical
#                               Intraday Signal System
#
# Signals and research only. Does NOT connect to Webull and does NOT
# place orders. Underlying crypto movement != event-contract P&L.
#
# V3 (mantis_15m_resolution_v3.py) is a PROTECTED BASELINE and is never
# modified by this program.
#
# PHASE 2 BUILD. There is no probability model yet, so every decision is
# NO TRADE. What this build DOES do is record every contract it observes
# and every outcome it can settle, so MANTIS starts accumulating its own
# clean dataset immediately.
# ================================================================

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

# UTF-8 safety on legacy Windows consoles (audit finding G-4). V3 relied on
# block-drawing glyphs with no encoding guard at all.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from mantis_v4 import __version__
    from mantis_v4.clock import Clock, LoopPacer, format_seconds
    from mantis_v4.config import MantisConfig, credential_status_lines
    from mantis_v4.engine import MantisEngine, ScanResult
    from mantis_v4.providers import (
        ContractProviderChain,
        LocalFileContractProvider,
        OfficialWebullOpenAPIProvider,
        UnderlyingProxyContractProvider,
        YFinanceMarketDataProvider,
    )
    from mantis_v4.recording import ContractRecorder
except ImportError as exc:
    missing = getattr(exc, "name", "a required package")
    print(f"\nMissing Python package: {missing}")
    print("Install project dependencies, then start MANTIS again:")
    print("    python -m pip install -r requirements.txt")
    raise SystemExit(1) from exc


BANNER = r"""
        __  \
   .-._/  \_.-.
  /___  __  ___\        MANTIS // 15M RESOLUTION V4
      \/  \/
   /\  .--.  /\         MARKET ANALYSIS AND NEURO-TACTICAL
  /  \/    \/  \        INTRADAY SIGNAL SYSTEM
 |   /  /\  \   |
 |  /__/  \__\  |       PHASE 2 - CONTRACT SEMANTICS,
  \   \__/   /          PROVIDERS, QUALITY GATES, RECORDING
   '._    _.'
      |__|
"""


def build_engine(config: MantisConfig, clock: Optional[Clock] = None) -> tuple:
    """Wire every Phase 2 subsystem together.

    Returns (engine, recorder, webull_provider) so callers can report health.
    """
    clock = clock or Clock()

    market_data = YFinanceMarketDataProvider(
        interval=config.bar_interval,
        bootstrap_period=config.bootstrap_period,
        refresh_period=config.refresh_period,
        min_candles=config.min_candles,
        max_cache_rows=config.max_cache_rows,
        timeout_seconds=config.network_timeout_seconds,
        max_retries=config.max_retries,
        backoff_seconds=config.retry_backoff_seconds,
    )

    webull = OfficialWebullOpenAPIProvider(config.credentials)
    local = LocalFileContractProvider(config.local_contract_path)

    recorder = ContractRecorder(
        config.ledger_db_path,
        contracts_csv=config.contracts_csv_path,
        predictions_csv=config.predictions_csv_path,
        model_version=f"v4-{__version__}",
    )

    # The proxy provider needs bars, which only the engine caches per scan.
    # A late-bound lookup avoids a circular dependency while still guaranteeing
    # one fetch per asset per scan.
    holder: dict = {}
    proxy = UnderlyingProxyContractProvider(
        lambda asset: holder["engine"]._lookup_bars(asset)
    )

    chain = ContractProviderChain([webull, local, proxy])

    engine = MantisEngine(
        config,
        clock=clock,
        market_data=market_data,
        contracts=chain,
        recorder=recorder,
    )
    holder["engine"] = engine

    return engine, recorder, webull


# ----------------------------------------------------------------------
# Reporting
# ----------------------------------------------------------------------

def print_status_block(config: MantisConfig, engine: MantisEngine, webull) -> None:
    print("=" * 74)
    print("  MANTIS V4 - SYSTEM STATUS")
    print("=" * 74)
    print(f"VERSION:              {__version__}")
    print(f"CONTRACT TIMEZONE:    {config.contract_timezone}")
    print(f"CONTRACT WINDOW:      {config.contract_window_minutes} minutes")
    print(f"ACTIVE ASSETS:        {', '.join(config.active_assets)}")
    print(f"SCAN INTERVAL:        {config.scan_interval_seconds:.1f}s (monotonic paced)")
    print(f"NETWORK TIMEOUT:      {config.network_timeout_seconds:.1f}s")
    print(f"MAX DATA AGE:         {config.max_data_age_seconds:.0f}s")
    print("-" * 74)
    for line in webull.status_lines():
        print(line)
    print("-" * 74)
    print("CONTRACT PROVIDER CHAIN (preference order):")
    for provider in engine.contracts.providers:
        print(f"  {provider.priority:>4}  {provider.name:<20} {provider.health.display}")
    print("-" * 74)
    if not engine.contracts.economics_provider_available():
        # The exact block the master prompt requires when economics are absent.
        print("REFERENCE:            PROXY / UNVERIFIED")
        print("CONTRACT ECONOMICS:   UNAVAILABLE")
        print("EV ENGINE:            DISABLED")
        print()
        print("MANTIS may still research directional probability, but it must")
        print("not call any trade 'positive EV' without real contract economics.")
    print("=" * 74)


def print_dataset_summary(recorder: ContractRecorder) -> None:
    summary = recorder.summary()
    print()
    print("-" * 74)
    print("  RECORDED DATASET (counts only - NOT a performance claim)")
    print("-" * 74)
    print(f"  contracts observed    : {summary['contracts_total']}")
    print(f"    open                : {summary['contracts_open']}")
    print(f"    resolved            : {summary['contracts_resolved']}")
    print(f"    unresolved (no data): {summary['contracts_unresolved']}")
    print(f"  contracts entered     : {summary['contracts_entered']}")
    print(f"  prediction rows       : {summary['prediction_rows']}")
    print("-" * 74)


def render_scan(result: ScanResult, config: MantisConfig) -> None:
    """Compact Phase 2 monitor table (section 26L shape, honest columns).

    Never prints an invented number: unavailable values render as N/A.
    The full Rich UI is Phase 9's job.
    """
    window = result.window
    print("\033[2J\033[H", end="")   # cheap clear that works without Rich
    print(BANNER)
    print(f"  {result.instant.astimezone(window.tz):%Y-%m-%d %I:%M:%S %p %Z}")
    print(f"  CONTRACT {window.label}   TIME LEFT {format_seconds(result.seconds_remaining)}")
    print(f"  WINDOW ID {window.contract_id}")
    print()

    header = (
        f"  {'ASSET':<9}{'SPOT':>15}{'REFERENCE':>15}{'SRC':>10}"
        f"{'BUFFER':>11}   {'STATUS'}"
    )
    print(header)
    print("  " + "-" * 88)

    for asset in config.active_assets:
        scan = result.assets.get(asset)
        if scan is None:
            print(
                f"  {asset:<9}{'N/A':>15}{'N/A':>15}{'N/A':>10}{'N/A':>11}   NOT SCANNED"
            )
            continue

        spot = "N/A" if scan.spot is None else f"{scan.spot:,.4f}"
        if scan.spec is not None and scan.spec.reference_price is not None:
            reference = f"{scan.spec.reference_price:,.4f}"
            source = "OFFICIAL" if scan.spec.reference_is_verified else "PROXY"
        else:
            reference = "N/A"
            source = "N/A"
        buffer_text = "N/A" if scan.buffer_pct is None else f"{scan.buffer_pct * 100:+.3f}%"

        print(
            f"  {asset:<9}{spot:>15}{reference:>15}{source:>10}{buffer_text:>11}"
            f"   {scan.status_text[:50]}"
        )

    print()
    ready = sum(1 for scan in result.assets.values() if scan.tradeable)
    print(
        f"  DECISION: NO TRADE on all assets (PHASE2_NO_MODEL). "
        f"{ready}/{len(result.assets)} passed the data-quality gate."
    )
    print("  Phase 2 has no probability model. This build observes and records only.")

    if result.resolved_contracts:
        print()
        print(f"  RESOLVED THIS SCAN: {len(result.resolved_contracts)}")
        for row in result.resolved_contracts[:6]:
            outcome = row.get("outcome_label") or f"UNRESOLVED ({row.get('unresolved_reason')})"
            terminal = row.get("terminal_price")
            terminal_text = "N/A" if terminal is None else f"{float(terminal):,.4f}"
            print(
                f"    {row['asset']:<9} {row['contract_id']:<28} "
                f"terminal={terminal_text:>13}  outcome={outcome}"
            )


# ----------------------------------------------------------------------
# Modes
# ----------------------------------------------------------------------

def run_selfcheck(config: MantisConfig) -> int:
    engine, recorder, webull = build_engine(config)
    try:
        print(BANNER)
        print_status_block(config, engine, webull)
        recovery = engine.startup_recovery()
        print()
        print("STARTUP RECOVERY (audit C-4: V3 lost live positions on restart):")
        print(f"  contracts open at startup : {recovery['open_at_startup']}")
        print(f"  already expired           : {recovery['expired_at_startup']}")
        print(f"  resolved during recovery  : {recovery['resolved_now']}")
        print(f"  still open (current window): {recovery['still_open']}")
        print_dataset_summary(recorder)
        print()
        print("SELF-CHECK COMPLETE. No orders were placed. V3 was not modified.")
        return 0
    finally:
        recorder.close()


def run_loop(config: MantisConfig, max_scans: Optional[int] = None) -> int:
    engine, recorder, webull = build_engine(config)
    clock = engine.clock
    pacer: LoopPacer = engine.pacer

    try:
        print(BANNER)
        print_status_block(config, engine, webull)
        recovery = engine.startup_recovery()
        print(
            f"\nSTARTUP RECOVERY: {recovery['resolved_now']} contract(s) resolved, "
            f"{recovery['still_open']} still open."
        )
        print("\nStarting scan loop. Ctrl-C to stop.\n")
        clock.sleep(1.5)

        scans = 0
        while max_scans is None or scans < max_scans:
            loop_start = clock.monotonic()
            try:
                result = engine.scan()
                render_scan(result, config)
            except Exception as exc:  # noqa: BLE001 - the loop must survive
                print(f"\n  SCAN ERROR (loop continues): {type(exc).__name__}: {exc}")
            scans += 1
            work = pacer.pace(loop_start)
            if pacer.overrun_count:
                print(
                    f"  [pacing] work={work:.2f}s interval={pacer.interval_seconds:.1f}s "
                    f"overruns={pacer.overrun_count}"
                )
        return 0

    except KeyboardInterrupt:
        print("\n\nMANTIS OFFLINE. No orders were placed.")
        return 0
    finally:
        # Resolve whatever we can before shutting down, so a Ctrl-C cannot
        # orphan a contract. Startup recovery is the backstop for the rest.
        try:
            engine.resolution.resolve_due(clock.capture())
        except Exception:  # noqa: BLE001
            pass
        print_dataset_summary(recorder)
        recorder.close()


def run_dataset_report(config: MantisConfig) -> int:
    recorder = ContractRecorder(
        config.ledger_db_path,
        contracts_csv=config.contracts_csv_path,
        predictions_csv=config.predictions_csv_path,
    )
    try:
        print(BANNER)
        print_dataset_summary(recorder)
        print()
        print("NOTE: these are dataset counts only. No accuracy, calibration or")
        print("profitability claim is made or implied. Those require Phase 3+ and")
        print("leakage-safe chronological out-of-sample testing.")
        return 0
    finally:
        recorder.close()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mantis_15m_resolution_v4",
        description=(
            "MANTIS V4 - 15-minute crypto event-contract research and decision "
            "support. Phase 2 build: observes and records only, never trades."
        ),
    )
    parser.add_argument(
        "--selfcheck", action="store_true",
        help="verify configuration, providers and storage, then exit",
    )
    parser.add_argument(
        "--dataset", action="store_true",
        help="print recorded dataset counts, then exit",
    )
    parser.add_argument(
        "--max-scans", type=int, default=None,
        help="stop after N scans (useful for smoke tests)",
    )
    parser.add_argument(
        "--config", type=Path, default=None,
        help="path to a local JSON config file",
    )
    args = parser.parse_args(argv)

    try:
        config = MantisConfig.load(local_file=args.config)
    except (ValueError, OSError) as exc:
        print(f"CONFIGURATION ERROR: {exc}")
        return 2

    if args.selfcheck:
        return run_selfcheck(config)
    if args.dataset:
        return run_dataset_report(config)
    return run_loop(config, max_scans=args.max_scans)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nMANTIS OFFLINE.")
        raise SystemExit(0)
