"""
MANTIS V4 — purged, embargoed walk-forward splits (Phase 3 requirement 6).

Phase 3 does not train models. It DOES have to leave behind split machinery
that Phase 5 cannot accidentally misuse, because the standard scikit-learn
splitters are actively wrong for this problem in two ways.

WHY A PLAIN CHRONOLOGICAL SPLIT IS NOT ENOUGH

1.  OVERLAPPING LABELS. A contract's label depends on prices over its whole
    15-minute window. Features for a scan at 11:03 are computed from bars that
    also inform the 10:45-11:00 contract's outcome. A training sample whose
    feature window overlaps a validation sample's label window leaks.
    PURGING drops training samples whose windows overlap the validation span.

2.  SERIAL CORRELATION. Even non-overlapping neighbours are correlated: 1-minute
    crypto returns cluster in volatility, so the contract immediately before the
    validation block carries information about it. An EMBARGO drops an
    additional span after the validation block.

A third hazard is specific to this system and is handled by the caller, not
here: CROSS-ASSET correlation. Phase 2 observed all five assets settling YES in
the same window, and the Phase 3 diagnostics quantify how strong that is. If
BTC and ETH contracts for the SAME window land on opposite sides of a split,
the split leaks even though each asset is chronologically clean. Use
``group_key='window'`` splitting for that; ``purged_walk_forward`` groups by
window start time, so same-window rows across assets always move together.

Nothing here is applied to a model yet. It is built and tested now so that
Phase 5 inherits a correct tool instead of improvising one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Optional, Sequence


@dataclass(frozen=True)
class LabelledSpan:
    """A training sample and the time span its label depends on.

    For MANTIS: ``start`` is the contract window start and ``end`` its
    resolution instant, because the outcome depends on prices across that whole
    interval.
    """

    index: int
    start: datetime
    end: datetime
    group: str = ""

    def overlaps(self, other_start: datetime, other_end: datetime) -> bool:
        """Half-open overlap test: [start, end) vs [other_start, other_end)."""
        return self.start < other_end and other_start < self.end


@dataclass
class Split:
    """One walk-forward fold after purging and embargo."""

    fold: int
    train_indices: list[int]
    test_indices: list[int]
    train_end: Optional[datetime] = None
    test_start: Optional[datetime] = None
    test_end: Optional[datetime] = None
    purged: int = 0
    embargoed: int = 0

    @property
    def train_size(self) -> int:
        return len(self.train_indices)

    @property
    def test_size(self) -> int:
        return len(self.test_indices)


def purged_walk_forward(
    spans: Sequence[LabelledSpan],
    *,
    folds: int = 5,
    embargo: timedelta = timedelta(minutes=15),
    expanding: bool = True,
    min_train: int = 1,
) -> list[Split]:
    """Chronological folds with purging and an embargo.

    ``expanding=True`` gives the growing-window scheme the master prompt
    describes (train 1-3 validate 4; train 1-4 validate 5; ...). ``False`` gives
    a rolling window.

    A training span is DROPPED when either:
      * it overlaps the test block's time span (purge), or
      * it starts within ``embargo`` after the test block ends (embargo).

    Training data strictly BEFORE the test block is what remains. Test blocks
    are contiguous and never shuffled.
    """
    if not spans:
        return []
    if folds < 2:
        raise ValueError("folds must be >= 2")

    ordered = sorted(spans, key=lambda s: (s.start, s.index))
    n = len(ordered)
    block = n // folds
    if block == 0:
        return []

    splits: list[Split] = []

    for fold in range(1, folds):
        test_lo = fold * block
        test_hi = n if fold == folds - 1 else (fold + 1) * block
        test_block = ordered[test_lo:test_hi]
        if not test_block:
            continue

        test_start = min(s.start for s in test_block)
        test_end = max(s.end for s in test_block)
        embargo_end = test_end + embargo

        candidates = ordered[:test_lo] if expanding else ordered[max(0, test_lo - block) : test_lo]

        train_indices: list[int] = []
        purged = 0
        embargoed = 0

        for span in candidates:
            if span.overlaps(test_start, test_end):
                purged += 1
                continue
            if test_end <= span.start < embargo_end:
                embargoed += 1
                continue
            train_indices.append(span.index)

        if len(train_indices) < min_train:
            continue

        splits.append(
            Split(
                fold=fold,
                train_indices=train_indices,
                test_indices=[s.index for s in test_block],
                train_end=max((s.end for s in ordered[:test_lo]), default=None),
                test_start=test_start,
                test_end=test_end,
                purged=purged,
                embargoed=embargoed,
            )
        )

    return splits


def spans_from_replays(replays: Iterable) -> list[LabelledSpan]:
    """Build label spans from ``ContractReplay`` objects.

    ``group`` is the window's contract_id so that every asset's row for the same
    15-minute window shares a group — the cross-asset hazard described above.
    """
    spans: list[LabelledSpan] = []
    for i, replay in enumerate(replays):
        spans.append(
            LabelledSpan(
                index=i,
                start=replay.window.start_utc,
                end=replay.window.end_utc,
                group=replay.window.contract_id,
            )
        )
    return spans


def assert_no_overlap(spans: Sequence[LabelledSpan], split: Split) -> None:
    """Verify a split really is leakage-free. Raises if not.

    Used by the tests, and cheap enough for Phase 5 to call on every fold.
    """
    by_index = {span.index: span for span in spans}
    test_spans = [by_index[i] for i in split.test_indices]
    if not test_spans:
        return
    test_start = min(s.start for s in test_spans)
    test_end = max(s.end for s in test_spans)

    for i in split.train_indices:
        span = by_index[i]
        if span.overlaps(test_start, test_end):
            raise AssertionError(
                f"LEAKAGE: train span {i} [{span.start}, {span.end}) overlaps "
                f"test block [{test_start}, {test_end})"
            )


def summarise(splits: Sequence[Split]) -> list[dict]:
    return [
        {
            "fold": s.fold,
            "train": s.train_size,
            "test": s.test_size,
            "purged": s.purged,
            "embargoed": s.embargoed,
            "test_start": s.test_start.isoformat() if s.test_start else None,
            "test_end": s.test_end.isoformat() if s.test_end else None,
        }
        for s in splits
    ]
