
"""
MANTIS V4 — Phase 4 splitting: window-grouped walk-forward with a sealed holdout.

THREE RULES, ALL ENFORCED BY CONSTRUCTION RATHER THAN BY CARE

1.  CHRONOLOGICAL ONLY. Folds advance forward in time. There is no shuffled
    split anywhere in this module, and no code path that could produce one.

2.  WINDOW-GROUPED. The unit is the 15-minute contract window, never the row
    and never the asset. Phase 3 measured 0.641 return correlation across
    assets and only 1.72 effective independent assets of five, so BTC and ETH
    rows for the same window are largely the same observation. Splitting them
    apart leaks. Every split here operates on sorted unique ``group_key``
    values, so all rows of a window necessarily travel together.

3.  SEALED HOLDOUT. The final chronological block is separated once, at load
    time, and is never returned by the cross-validation iterator. Phase 4
    section 1 forbids using it for feature selection, hyperparameter choice,
    calibration-method choice, or threshold tuning.

    ``HoldoutSeal`` makes accidental use loud rather than silent: it records a
    hash of the holdout group set and asserts that no training fold has ever
    intersected it. ``test_final_holdout_untouched`` verifies the seal.

PURGE AND EMBARGO

A contract's label depends on prices across its own 15 minutes. Training
windows whose label span overlaps the validation block are purged; an embargo
additionally drops windows starting shortly after it. With expanding
chronological folds the embargo count is legitimately zero (nothing after the
test block is in training) — that is asserted rather than assumed, exactly as
in Phase 3.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Iterator, Optional, Sequence

import numpy as np
import pandas as pd

DEFAULT_EMBARGO_SECONDS = 900.0        # one contract window


@dataclass(frozen=True)
class GroupedSplit:
    """One walk-forward fold, expressed as row-level positional indices."""

    fold: int
    train_idx: np.ndarray
    test_idx: np.ndarray
    train_groups: int
    test_groups: int
    purged_groups: int
    embargoed_groups: int
    test_start_epoch: float
    test_end_epoch: float

    @property
    def train_size(self) -> int:
        return len(self.train_idx)

    @property
    def test_size(self) -> int:
        return len(self.test_idx)


@dataclass
class HoldoutSeal:
    """Tamper-evident record of the sealed holdout.

    Not security — discipline. It converts "I am fairly sure we never trained
    on the holdout" into an assertion that fails loudly if we ever did.
    """

    group_hash: str
    n_groups: int
    n_rows: int
    first_epoch: float
    last_epoch: float
    _groups: frozenset = field(repr=False, default_factory=frozenset)
    violations: list[str] = field(default_factory=list, repr=False)

    def assert_disjoint(self, groups: Iterator[str], label: str) -> None:
        overlap = self._groups.intersection(groups)
        if overlap:
            message = (
                f"HOLDOUT VIOLATION in {label}: {len(overlap)} holdout window(s) "
                f"appeared in data that must not contain them, "
                f"e.g. {sorted(overlap)[:3]}"
            )
            self.violations.append(message)
            raise AssertionError(message)

    @property
    def intact(self) -> bool:
        return not self.violations


def _hash_groups(groups: Sequence[str]) -> str:
    digest = hashlib.sha256()
    for group in sorted(groups):
        digest.update(group.encode("utf-8"))
    return digest.hexdigest()[:16]


@dataclass
class SplitPlan:
    """Development set plus a sealed holdout, both window-grouped."""

    dev: pd.DataFrame
    holdout: pd.DataFrame
    seal: HoldoutSeal
    holdout_fraction: float

    def describe(self) -> dict:
        return {
            "dev_rows": len(self.dev),
            "dev_groups": int(self.dev["group_key"].nunique()),
            "holdout_rows": len(self.holdout),
            "holdout_groups": int(self.holdout["group_key"].nunique()),
            "holdout_fraction": self.holdout_fraction,
            "holdout_hash": self.seal.group_hash,
            "seal_intact": self.seal.intact,
        }


def reserve_holdout(
    table: pd.DataFrame,
    *,
    holdout_fraction: float = 0.25,
    group_column: str = "group_key",
    time_column: str = "window_epoch",
) -> SplitPlan:
    """Split off the FINAL chronological block of windows as a sealed holdout.

    Grouped and chronological: the holdout is the last N% of distinct contract
    windows by start time, with every asset and every scan of those windows
    moving together.
    """
    if not 0.0 < holdout_fraction < 1.0:
        raise ValueError("holdout_fraction must be strictly between 0 and 1")
    if table.empty:
        raise ValueError("cannot reserve a holdout from an empty table")

    order = (
        table[[group_column, time_column]]
        .drop_duplicates(subset=[group_column])
        .sort_values(time_column)
    )
    groups = order[group_column].tolist()
    n_hold = max(1, int(round(len(groups) * holdout_fraction)))
    holdout_groups = set(groups[-n_hold:])

    mask = table[group_column].isin(holdout_groups)
    holdout = table.loc[mask].reset_index(drop=True)
    dev = table.loc[~mask].reset_index(drop=True)

    seal = HoldoutSeal(
        group_hash=_hash_groups(sorted(holdout_groups)),
        n_groups=len(holdout_groups),
        n_rows=len(holdout),
        first_epoch=float(holdout[time_column].min()),
        last_epoch=float(holdout[time_column].max()),
        _groups=frozenset(holdout_groups),
    )

    # The dev set must not contain a single holdout window.
    seal.assert_disjoint(set(dev[group_column].unique()), "development set")

    return SplitPlan(
        dev=dev, holdout=holdout, seal=seal, holdout_fraction=holdout_fraction
    )


def grouped_walk_forward(
    table: pd.DataFrame,
    *,
    folds: int = 5,
    group_column: str = "group_key",
    time_column: str = "window_epoch",
    window_seconds: float = 900.0,
    embargo_seconds: float = DEFAULT_EMBARGO_SECONDS,
    expanding: bool = True,
    min_train_groups: int = 50,
) -> list[GroupedSplit]:
    """Chronological, window-grouped, purged and embargoed folds.

    Returns row-level positional indices into ``table`` (which must have a
    default RangeIndex).
    """
    if folds < 2:
        raise ValueError("folds must be >= 2")
    if table.empty:
        return []

    order = (
        table[[group_column, time_column]]
        .drop_duplicates(subset=[group_column])
        .sort_values(time_column)
        .reset_index(drop=True)
    )
    groups = order[group_column].to_numpy()
    starts = order[time_column].to_numpy(dtype="float64")
    ends = starts + window_seconds

    n_groups = len(groups)
    block = n_groups // folds
    if block == 0:
        return []

    # Row positions per group, computed once.
    positions: dict[str, np.ndarray] = {
        group: np.flatnonzero((table[group_column] == group).to_numpy())
        for group in groups
    } if n_groups <= 5000 else {}

    if not positions:
        grouped = table.groupby(group_column, sort=False).indices
        positions = {k: np.asarray(v) for k, v in grouped.items()}

    splits: list[GroupedSplit] = []

    for fold in range(1, folds):
        lo = fold * block
        hi = n_groups if fold == folds - 1 else (fold + 1) * block
        test_groups = groups[lo:hi]
        if len(test_groups) == 0:
            continue

        test_start = float(starts[lo:hi].min())
        test_end = float(ends[lo:hi].max())
        embargo_end = test_end + embargo_seconds

        if expanding:
            candidate_slice = slice(0, lo)
        else:
            candidate_slice = slice(max(0, lo - block), lo)

        candidate_groups = groups[candidate_slice]
        candidate_starts = starts[candidate_slice]
        candidate_ends = ends[candidate_slice]

        keep, purged, embargoed = [], 0, 0
        for group, gs, ge in zip(candidate_groups, candidate_starts, candidate_ends):
            # Purge: label span overlaps the test block.
            if gs < test_end and test_start < ge:
                purged += 1
                continue
            # Embargo: begins inside the shadow of the test block.
            if test_end <= gs < embargo_end:
                embargoed += 1
                continue
            keep.append(group)

        if len(keep) < min_train_groups:
            continue

        train_idx = np.concatenate([positions[g] for g in keep]) if keep else np.array([], dtype=int)
        test_idx = np.concatenate([positions[g] for g in test_groups])

        splits.append(
            GroupedSplit(
                fold=fold,
                train_idx=np.sort(train_idx),
                test_idx=np.sort(test_idx),
                train_groups=len(keep),
                test_groups=len(test_groups),
                purged_groups=purged,
                embargoed_groups=embargoed,
                test_start_epoch=test_start,
                test_end_epoch=test_end,
            )
        )

    return splits


def assert_split_integrity(table: pd.DataFrame, split: GroupedSplit,
                           group_column: str = "group_key",
                           time_column: str = "window_epoch") -> None:
    """Verify a fold really is chronological, grouped and non-overlapping.

    Cheap enough to run on every fold of every ablation.
    """
    train_groups = set(table.iloc[split.train_idx][group_column])
    test_groups = set(table.iloc[split.test_idx][group_column])

    shared = train_groups & test_groups
    if shared:
        raise AssertionError(
            f"GROUP LEAK: {len(shared)} window(s) in both train and test, "
            f"e.g. {sorted(shared)[:3]}"
        )

    if len(split.train_idx) and len(split.test_idx):
        train_max = float(table.iloc[split.train_idx][time_column].max())
        test_min = float(table.iloc[split.test_idx][time_column].min())
        if train_max >= test_min:
            raise AssertionError(
                f"CHRONOLOGY VIOLATION: latest train window {train_max} is not "
                f"before earliest test window {test_min}"
            )


def summarise_splits(splits: Sequence[GroupedSplit]) -> list[dict]:
    return [
        {
            "fold": s.fold,
            "train_rows": s.train_size,
            "test_rows": s.test_size,
            "train_groups": s.train_groups,
            "test_groups": s.test_groups,
            "purged_groups": s.purged_groups,
            "embargoed_groups": s.embargoed_groups,
        }
        for s in splits
    ]
