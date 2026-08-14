"""
MANTIS V4 — historical dataset schema and writer (Phase 3 requirement 7).

One row per (contract, asset, strategy). Every column the requirement lists is
present, plus the provenance flags that keep this dataset honest.

THE THREE PINNED COLUMNS

    reference_verified   always 0
    reference_source     always PROXY_WINDOW_OPEN
    economics_available  always 0

Phase 3 requirement 1 mandates these for anything reconstructed from an
underlying-only source. They are not defaults that a caller may override —
``write_dataset`` asserts them on every row and raises if one is ever anything
else. A dataset that quietly loses its proxy flag would let a later phase
report proxy-settled accuracy as though it were venue accuracy, which is the
single most damaging mistake available here.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from ..clock import UTC
from .features import FEATURE_COLUMNS

SCHEMA_VERSION = 1

# Identity, contract, decision and outcome columns.
CORE_COLUMNS = [
    "schema_version",
    "strategy",
    "contract_id",
    "asset",
    "contract_start_utc",
    "contract_end_utc",
    "contract_start_local",
    "contract_end_local",
    "timezone",
    "window_minutes",
    # reference / provenance (pinned)
    "reference",
    "reference_verified",
    "reference_source",
    "reference_bar_utc",
    "settlement_rule",
    "economics_available",
    # decision
    "scan_count",
    "decision",
    "entered",
    "entry_side",
    "entry_timestamp",
    "seconds_remaining",         # at entry (or None)
    "spot",                      # at entry (or None)
    "buffer",                    # at entry (or None)
    "buffer_pct",
    "entry_p_yes",
    "entry_reason",
    # outcome
    "terminal_price",
    "terminal_source",
    "terminal_bar_utc",
    "outcome_yes",
    "outcome_label",
    "prediction_correct",
    # quality / context
    "data_quality_status",
    "usable",
    "skip_reason",
    "regime",
]

# Feature snapshot at the decision instant, prefixed to avoid collisions.
FEATURE_PREFIX = "f_"
DATASET_COLUMNS = CORE_COLUMNS + [f"{FEATURE_PREFIX}{name}" for name in FEATURE_COLUMNS]


class ProvenanceViolation(RuntimeError):
    """Raised when a row would claim verified data it does not have."""


def _iso(moment: Optional[datetime]) -> Optional[str]:
    return None if moment is None else moment.astimezone(UTC).isoformat()


def replay_to_row(replay, settlement_rule: str) -> dict[str, Any]:
    """Flatten one ``ContractReplay`` into a dataset row."""
    window = replay.window
    features = replay.entry_features or {}

    row: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "strategy": replay.strategy,
        "contract_id": replay.contract_id,
        "asset": replay.asset,
        "contract_start_utc": _iso(window.start_utc),
        "contract_end_utc": _iso(window.end_utc),
        "contract_start_local": window.start_local.isoformat(),
        "contract_end_local": window.end_local.isoformat(),
        "timezone": window.tz_name,
        "window_minutes": int(window.total_seconds // 60),

        "reference": replay.reference,
        # Pinned. Asserted below and again in write_dataset.
        "reference_verified": 0,
        "reference_source": "PROXY_WINDOW_OPEN",
        "reference_bar_utc": _iso(replay.reference_bar_utc),
        "settlement_rule": settlement_rule,
        "economics_available": 0,

        "scan_count": replay.scan_count,
        "decision": replay.decision,
        "entered": int(bool(replay.entered)),
        "entry_side": replay.entry_side,
        "entry_timestamp": _iso(replay.entry_timestamp),
        "seconds_remaining": features.get("seconds_remaining"),
        "spot": replay.entry_spot,
        "buffer": features.get("buffer_abs"),
        "buffer_pct": features.get("buffer_pct"),
        "entry_p_yes": replay.entry_p_yes,
        "entry_reason": replay.entry_reason,

        "terminal_price": replay.terminal_price,
        "terminal_source": replay.terminal_source,
        "terminal_bar_utc": _iso(replay.terminal_bar_utc),
        "outcome_yes": None if replay.outcome_yes is None else int(replay.outcome_yes),
        "outcome_label": replay.outcome_label,
        "prediction_correct": replay.prediction_correct,

        "data_quality_status": replay.data_quality_status,
        "usable": int(bool(replay.usable)),
        "skip_reason": replay.skip_reason,
        # Placeholder per requirement 7. Phase 7 owns regime classification;
        # emitting a guess here would be a fabricated feature.
        "regime": replay.regime,
    }

    for name in FEATURE_COLUMNS:
        row[f"{FEATURE_PREFIX}{name}"] = features.get(name)

    return row


def assert_provenance(row: dict[str, Any]) -> None:
    """Refuse any row that claims verified data or available economics."""
    if row.get("reference_verified") not in (0, False):
        raise ProvenanceViolation(
            f"reference_verified must be 0 for underlying-derived rows, "
            f"got {row.get('reference_verified')!r} "
            f"(contract {row.get('contract_id')}, asset {row.get('asset')})"
        )
    if row.get("economics_available") not in (0, False):
        raise ProvenanceViolation(
            f"economics_available must be 0 without historical contract prices, "
            f"got {row.get('economics_available')!r}"
        )
    if row.get("reference_source") != "PROXY_WINDOW_OPEN":
        raise ProvenanceViolation(
            f"reference_source must be PROXY_WINDOW_OPEN, "
            f"got {row.get('reference_source')!r}"
        )


@dataclass
class DatasetWriteReport:
    path: Path
    rows: int
    usable_rows: int
    entered_rows: int
    assets: list[str]
    strategies: list[str]


def write_dataset(
    rows: Iterable[dict[str, Any]],
    path: Path,
    *,
    columns: Sequence[str] = DATASET_COLUMNS,
) -> DatasetWriteReport:
    """Write the dataset to CSV, enforcing provenance on every row."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    materialised = list(rows)
    for row in materialised:
        assert_provenance(row)

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), extrasaction="ignore")
        writer.writeheader()
        for row in materialised:
            writer.writerow(row)

    return DatasetWriteReport(
        path=path,
        rows=len(materialised),
        usable_rows=sum(1 for r in materialised if r.get("usable")),
        entered_rows=sum(1 for r in materialised if r.get("entered")),
        assets=sorted({r["asset"] for r in materialised}),
        strategies=sorted({r["strategy"] for r in materialised}),
    )


def write_schema_documentation(path: Path) -> Path:
    """Emit machine-readable schema documentation alongside the data."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    doc = {
        "schema_version": SCHEMA_VERSION,
        "grain": "one row per (contract_id, asset, strategy)",
        "provenance": {
            "reference_verified": "ALWAYS 0 - proxy reference, never venue-verified",
            "reference_source": "ALWAYS PROXY_WINDOW_OPEN",
            "economics_available": "ALWAYS 0 - no historical contract prices exist",
            "settlement_rule": "PROXY_TERMINAL_ABOVE_REFERENCE - research label only",
            "enforcement": "write_dataset() raises ProvenanceViolation otherwise",
        },
        "leakage_controls": {
            "visible_bars": "bar.index + 60s <= scan_utc (completed bars only)",
            "reference": "Open of first bar at/after window start; exposed from that bar's timestamp",
            "entry_snapshot": "deep-copied at entry, never rewritten",
            "settlement": "computed only after the scan loop ends",
        },
        "known_divergence_from_live": (
            "Backtest spot is the last COMPLETED bar close, up to 59s stale on a "
            "15s scan grid. Live sees the in-progress bar. The backtest therefore "
            "has strictly less information than live, so results are conservative."
        ),
        "columns": {
            "core": CORE_COLUMNS,
            "features": [f"{FEATURE_PREFIX}{name}" for name in FEATURE_COLUMNS],
        },
        "notes": {
            "prediction_correct": "NULL when no side was taken; a NO TRADE is neither correct nor incorrect",
            "regime": "placeholder, populated in Phase 7",
            "f_v3_rsi": "V3-BUG-FAITHFUL: returns 50 on a zero-loss window (audit D-9)",
            "f_v3_realized_vol_1m": "V3-BUG-FAITHFUL: log returns span index gaps (audit D-5)",
            "f_rsi / f_realized_vol_1m": "corrected V4 versions",
        },
    }
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return path
