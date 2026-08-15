"""Accelerated deterministic Phase 10 forward-runtime soak harness.

All files live in a temporary directory. No provider, UI profile, browser, or
real forward path is opened.
"""
from __future__ import annotations

import argparse
import tempfile
import threading
import tracemalloc
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .forward import ForwardEngine, ForwardStore, LiveAssetState, ReferenceStatus

UTC = timezone.utc


@dataclass(frozen=True)
class SoakReport:
    windows: int
    scans: int
    observations: int
    entries: int
    resolutions: int
    duplicate_entries: int
    unresolved: int
    thread_delta: int
    peak_memory_bytes: int

    @property
    def passed(self) -> bool:
        return (self.observations == self.scans and self.entries == self.windows
                and self.resolutions == self.windows and self.duplicate_entries == 0
                and self.unresolved == 0 and self.thread_delta == 0)


def run_soak(windows: int = 250, scans_per_window: int = 2) -> SoakReport:
    if windows < 1 or scans_per_window < 1:
        raise ValueError("windows and scans_per_window must be positive")
    before_threads = threading.active_count()
    tracemalloc.start()
    with tempfile.TemporaryDirectory() as temporary:
        store = ForwardStore(Path(temporary))
        engine = ForwardEngine(store, run_id="PHASE10-SOAK")
        origin = datetime(2026, 1, 1, tzinfo=UTC)
        for window_index in range(windows):
            start = origin + timedelta(minutes=15 * window_index)
            end = start + timedelta(minutes=15)
            contract = f"SOAK-{window_index:06d}"
            for scan_index in range(scans_per_window):
                # Inside the locked Phase 6 entry window (T-240/T-210).
                instant = end - timedelta(seconds=240 - scan_index * 30)
                state = LiveAssetState(
                    asset="BTC-USD", contract_id=contract, window_start=start, window_end=end,
                    scan_timestamp=instant, reference=100.0,
                    reference_status=ReferenceStatus.PROXY, current_price=102.0,
                    volatility_estimate=0.01, p_yes=0.99, conservative_bound=0.95,
                    fragility=10.0, disagreement=0.01, crossing_probability=0.01,
                    reference_crossings=0, data_age_seconds=0.0,
                    fetch_latency_seconds=0.01, quality_ok=True, quality_reason="SOAK")
                engine.process(state)
            engine.resolve(asset="BTC-USD", contract_id=contract,
                           resolution_timestamp=end, terminal_value=103.0, reference=100.0)
        observations = store.read("observations")
        entries = store.read("entries")
        resolutions = store.read("resolutions")
        keys = [(row["contract_id"], row["asset"]) for row in entries]
        report = SoakReport(
            windows=windows, scans=windows * scans_per_window,
            observations=len(observations), entries=len(entries), resolutions=len(resolutions),
            duplicate_entries=len(keys) - len(set(keys)), unresolved=len(store.unresolved_entries()),
            thread_delta=threading.active_count() - before_threads,
            peak_memory_bytes=tracemalloc.get_traced_memory()[1])
    tracemalloc.stop()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="isolated accelerated MANTIS soak test")
    parser.add_argument("--windows", type=int, default=250)
    parser.add_argument("--scans-per-window", type=int, default=2)
    args = parser.parse_args()
    report = run_soak(args.windows, args.scans_per_window)
    for key, value in report.__dict__.items():
        print(f"{key.upper():<24} {value}")
    print("RESULT", "PASS" if report.passed else "FAIL")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
