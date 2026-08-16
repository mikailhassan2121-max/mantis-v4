"""Bounded, read-only validation study for KALSHI_REFERENCE_V1.

No live selector, voice, UI, forward store, authentication, or order endpoint is
reachable from this script.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
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
from mantis_v4.economics.kalshi import KalshiPublicHttpClient, SERIES_BY_ASSET
from mantis_v4.economics.kalshi_reference import (
    CURRENT_VALUE_PROVENANCE, ENDPOINT_MODEL, GATE_STATUS, MODEL_POLICY,
    QUALIFICATION_POLICY, SETTLEMENT_PROVENANCE, TRANSFERRED_POLICY,
    HistoricalKalshiContract, build_kalshi_states, chronological_three_way_split,
    fetch_settled_contracts,
)
from mantis_v4.entry import apply_policy_once, evaluate_entries
from mantis_v4.models.dataset_builder import ModellingDatasetBuilder
from mantis_v4.providers.market_data import normalize_bars
from mantis_v4_phase6 import build_states as build_proxy_states


def cached_frames(history: Path, assets: list[str]) -> dict[str, pd.DataFrame]:
    frames = {}
    for asset in assets:
        pieces = []
        for path in sorted(history.glob(f"{asset}_1m_*.csv")):
            frame = pd.read_csv(path, index_col=0, parse_dates=True)
            frame.index = pd.DatetimeIndex(frame.index)
            if frame.index.tz is None:
                frame.index = frame.index.tz_localize("UTC")
            else:
                frame.index = frame.index.tz_convert("UTC")
            pieces.append(normalize_bars(frame))
        if pieces:
            frame = pd.concat(pieces).sort_index()
            frames[asset] = frame[~frame.index.duplicated(keep="last")]
    return frames


def target_crossings(table: pd.DataFrame, frames: dict[str, pd.DataFrame]) -> np.ndarray:
    result = pd.Series(0, index=table.index, dtype="int64")
    for (_, asset), block in table.groupby(["contract_id", "asset"], sort=False):
        frame = frames[str(asset)]
        start = pd.Timestamp(block.window_start_utc.iloc[0])
        target = float(block.kalshi_target.iloc[0])
        window = frame.loc[(frame.index >= start) & (frame.index <= pd.Timestamp(block.scan_utc.max())), "Close"]
        signs = (window.to_numpy(dtype=float) >= target).astype("int8")
        changes = np.r_[0, np.cumsum(signs[1:] != signs[:-1])] if len(signs) else np.array([], dtype=int)
        for idx, scan in zip(block.index, pd.to_datetime(block.scan_utc, utc=True)):
            pos = window.index.searchsorted(scan, side="right") - 1
            result.at[idx] = int(changes[pos]) if pos >= 0 else 0
    return result.to_numpy()


def proper_scores(entries: pd.DataFrame) -> dict:
    traded = entries[entries.entered.astype(bool)].copy()
    if traded.empty:
        return {"brier": None, "log_loss": None, "calibration": []}
    p = np.clip(traded.p_yes.to_numpy(dtype=float), 1e-12, 1 - 1e-12)
    y = traded.outcome_yes.to_numpy(dtype=float)
    bands = [0, .1, .2, .3, .4, .5, .6, .7, .8, .9, .95, .975, 1.000001]
    labels = []
    confidence = np.maximum(p, 1 - p)
    correct = np.where(p >= .5, y == 1, y == 0).astype(float)
    for lo, hi in zip(bands[:-1], bands[1:]):
        mask = (confidence >= lo) & (confidence < hi)
        if mask.any():
            labels.append({"band": f"{lo:.3f}-{min(hi,1):.3f}", "n": int(mask.sum()),
                           "mean_confidence": float(confidence[mask].mean()),
                           "empirical_accuracy": float(correct[mask].mean())})
    return {"brier": float(np.mean((p - y) ** 2)),
            "log_loss": float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))),
            "calibration": labels}


def rows_for(contracts):
    return [
        {"asset": c.asset, "series_ticker": c.series_ticker,
         "market_ticker": c.market_ticker, "event_ticker": c.event_ticker,
         "window_start_utc": c.window_start_utc.isoformat(),
         "window_end_utc": c.window_end_utc.isoformat(), "target": str(c.target),
         "ending_benchmark": str(c.ending_benchmark) if c.ending_benchmark is not None else None,
         "outcome_yes": c.outcome_yes, "result": c.result,
         "settlement_reference_provenance": c.settlement_reference_provenance}
        for c in contracts
    ]


def contracts_from_csv(path: Path):
    rows = pd.read_csv(path)
    result = []
    for row in rows.itertuples(index=False):
        result.append(HistoricalKalshiContract(
            str(row.asset), str(row.series_ticker), str(row.market_ticker), str(row.event_ticker),
            pd.Timestamp(row.window_start_utc).to_pydatetime(),
            pd.Timestamp(row.window_end_utc).to_pydatetime(),
            __import__("decimal").Decimal(str(row.target)),
            None if pd.isna(row.ending_benchmark) else __import__("decimal").Decimal(str(row.ending_benchmark)),
            int(row.outcome_yes), str(row.result)))
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-pages", type=int, default=5)
    ap.add_argument("--reuse-markets", action="store_true",
                    help="reuse the previously captured public market metadata")
    args = ap.parse_args()
    root = ROOT
    assets = list(SERIES_BY_ASSET)
    market_path = root / "data" / "kalshi_reference_v1_markets.csv"
    if args.reuse_markets and market_path.exists():
        contracts = contracts_from_csv(market_path)
    else:
        client = KalshiPublicHttpClient()
        contracts = []
        for asset, series in SERIES_BY_ASSET.items():
            contracts.extend(fetch_settled_contracts(client, asset=asset, series_ticker=series,
                                                     max_pages_per_tier=args.max_pages))
        contracts.sort(key=lambda c: (c.window_end_utc, c.asset))
        pd.DataFrame(rows_for(contracts)).to_csv(market_path, index=False)

    frames = cached_frames(root / "data" / "history", assets)
    if set(frames) != set(assets):
        raise SystemExit(f"missing cached history for {sorted(set(assets)-set(frames))}")
    provider = InMemoryHistoryProvider(frames)
    loaded, _ = load_universe(provider, assets, 10_000)
    cfg = MantisConfig.load()
    table, build_report = ModellingDatasetBuilder(loaded, load_timezone(cfg.contract_timezone)).build(assets)

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
    joined["reference_gap"] = joined.kalshi_target - joined.old_proxy_reference
    joined["reference_gap_bps"] = joined.reference_gap / joined.old_proxy_reference * 10000

    kalshi_states = build_kalshi_states(joined)
    proxy_table = joined.copy()
    proxy_table["outcome_yes"] = joined.outcome_yes
    proxy_states = build_proxy_states(proxy_table)
    kalshi_entries = apply_policy_once(kalshi_states, TRANSFERRED_POLICY)
    proxy_entries = apply_policy_once(proxy_states, TRANSFERRED_POLICY)

    contract_gaps = joined.groupby(["contract_id", "asset"], as_index=False).first()
    matched_keys = set(zip(contract_gaps.asset, pd.to_datetime(contract_gaps.window_end_utc, utc=True)))
    matched_contracts = [c for c in contracts if (c.asset, pd.Timestamp(c.window_end_utc)) in matched_keys]
    dev_c, val_c, hold_c = chronological_three_way_split(matched_contracts)
    split_windows = {
        "development": {x.window_end_utc for x in dev_c},
        "validation": {x.window_end_utc for x in val_c},
        "sealed_holdout": {x.window_end_utc for x in hold_c},
    }
    end_by_group = joined[["group_key", "window_end_utc"]].drop_duplicates().set_index("group_key").window_end_utc
    results = {}
    for name, windows in split_windows.items():
        groups = set(end_by_group[end_by_group.isin(windows)].index)
        subset = kalshi_entries[kalshi_entries.group_key.isin(groups)]
        results[name] = {**evaluate_entries(subset, len(subset), n_boot=400), **proper_scores(subset)}

    joined_key = ["contract_id", "asset"]
    compare = kalshi_entries.merge(proxy_entries, on=joined_key, suffixes=("_kalshi", "_proxy"))
    both_side = compare.side_kalshi.notna() & compare.side_proxy.notna()
    side_flip = float((compare.loc[both_side, "side_kalshi"] != compare.loc[both_side, "side_proxy"]).mean()) if both_side.any() else None
    state_side_flip = float(((kalshi_states.p_yes >= .5) != (proxy_states.p_yes >= .5)).mean())
    qualification_change = float((compare.entered_kalshi != compare.entered_proxy).mean()) if len(compare) else None

    summary = {
        "model_policy": MODEL_POLICY, "qualification_policy": QUALIFICATION_POLICY,
        "gate_status": GATE_STATUS, "thresholds_retuned": False,
        "current_value_provenance": CURRENT_VALUE_PROVENANCE,
        "endpoint_model": ENDPOINT_MODEL,
        "settlement_reference_provenance": SETTLEMENT_PROVENANCE,
        "public_contracts_reconstructed": len(contracts),
        "contracts_matched_to_local_causal_bars": int(contract_gaps.shape[0]),
        "state_rows": len(joined), "dataset_build_report": asdict(build_report),
        "first_market_utc": contracts[0].window_start_utc.isoformat() if contracts else None,
        "last_market_utc": contracts[-1].window_end_utc.isoformat() if contracts else None,
        "reference_gap": {
            asset: {"n": int(len(x)), "mean_bps": float(x.reference_gap_bps.mean()),
                    "median_abs_bps": float(x.reference_gap_bps.abs().median()),
                    "p95_abs_bps": float(x.reference_gap_bps.abs().quantile(.95))}
            for asset, x in contract_gaps.groupby("asset")},
        "side_flip_frequency_all_states": state_side_flip,
        "side_flip_frequency_when_both_enter": side_flip,
        "qualification_change_frequency": qualification_change,
        "splits": {name: {"windows": len(windows), **results[name]} for name, windows in split_windows.items()},
        "holdout_window_hash": hashlib.sha256("\n".join(sorted(x.isoformat() for x in split_windows["sealed_holdout"])).encode()).hexdigest()[:16],
        "limitations": ["EXPERIMENTAL POLICY", "YAHOO CURRENT VALUE IS A PROXY FOR CF BENCHMARKS",
                        "TERMINAL-PRICE VARIANCE APPROXIMATES THE 60-SECOND ENDPOINT AVERAGE",
                        "TRANSFERRED GATES ARE NOT RETUNED OR VALIDATED FOR LIVE ACTION"],
    }
    out = root / "data" / "kalshi_reference_v1_summary.json"
    out.write_text(json.dumps(summary, indent=2, allow_nan=False, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=2, allow_nan=False, default=str))


if __name__ == "__main__":
    main()
