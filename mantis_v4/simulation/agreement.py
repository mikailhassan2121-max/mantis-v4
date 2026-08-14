"""
MANTIS V4 — model agreement and disagreement (Phase 5 section 7).

Phase 5 does NOT build the production ensemble. Section 13 forbids it. What
this module does is measure whether DISAGREEMENT between independent
probability channels carries information — specifically:

    "Are losing high-confidence Normal-Z predictions associated with greater
     disagreement?"

That is an empirical question with a yes/no answer, and it is tested rather
than assumed. If disagreement does not separate winners from losers, it stays a
displayed diagnostic and nothing more.

WHY DISAGREEMENT MIGHT CARRY INFORMATION

The channels fail in different ways. Normal-Z assumes Gaussian tails; Student-t
assumes fatter ones; the empirical and bootstrap channels assume the recent
past resembles the near future; Monte Carlo inherits whichever innovation
distribution it was given. When all four land in the same place, the answer is
robust to the assumption that separates them. When they scatter, at least one
assumption is doing heavy lifting — and there is no way to know which.

WHY IT MIGHT NOT

The channels are not independent in the way that argument needs. All four are
driven by the same buffer and the same sigma estimate, so they share their
dominant input and can be confidently wrong together. Disagreement can only
detect DISTRIBUTIONAL uncertainty, never an error in sigma or in the reference.

That is a real limitation and the reason this is measured rather than trusted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np


@dataclass
class AgreementResult:
    """Cross-channel summary for each row."""

    channels: list[str]
    mean: np.ndarray
    median: np.ndarray
    minimum: np.ndarray
    maximum: np.ndarray
    std: np.ndarray
    spread: np.ndarray                 # max - min
    disagreement: np.ndarray           # spread of the DECISION-side probability
    n_channels: np.ndarray
    lower_bound: Optional[np.ndarray] = None
    diagnostics: dict = field(default_factory=dict)

    def validate(self) -> "AgreementResult":
        for name in ("mean", "median", "minimum", "maximum"):
            array = getattr(self, name)
            finite = np.isfinite(array)
            if finite.any():
                block = array[finite]
                if np.any(block < 0.0) or np.any(block > 1.0):
                    raise ValueError(f"agreement.{name} outside [0, 1]")
        return self


def combine_channels(
    channels: dict[str, np.ndarray],
    *,
    anchor: str = "gaussian",
) -> AgreementResult:
    """Summarise several probability vectors row by row.

    NaN-tolerant: a channel that could not produce an estimate for a row (the
    bootstrap's INSUFFICIENT SAMPLE case) is excluded from that row's summary
    rather than silently treated as 0.5.
    """
    names = list(channels)
    if not names:
        raise ValueError("no channels supplied")

    stack = np.vstack([np.asarray(channels[n], dtype="float64") for n in names])
    n_rows = stack.shape[1]

    with np.errstate(invalid="ignore"):
        mean = np.nanmean(stack, axis=0)
        median = np.nanmedian(stack, axis=0)
        minimum = np.nanmin(stack, axis=0)
        maximum = np.nanmax(stack, axis=0)
        std = np.nanstd(stack, axis=0, ddof=0)

    n_channels = np.isfinite(stack).sum(axis=0).astype("float64")
    spread = maximum - minimum

    # Disagreement is measured on the side the system would actually take,
    # decided by the anchor. Raw spread in p-space would call a 0.02/0.06
    # disagreement (both firmly NO) the same as a 0.48/0.52 one (a coin flip
    # either way), which is the opposite of useful.
    anchor_p = np.asarray(channels.get(anchor, channels[names[0]]), dtype="float64")
    take_yes = anchor_p >= 0.5
    side_stack = np.where(take_yes[None, :], stack, 1.0 - stack)
    with np.errstate(invalid="ignore"):
        disagreement = np.nanmax(side_stack, axis=0) - np.nanmin(side_stack, axis=0)

    result = AgreementResult(
        channels=names,
        mean=mean, median=median, minimum=minimum, maximum=maximum,
        std=std, spread=spread, disagreement=disagreement,
        n_channels=n_channels,
    )
    result.diagnostics["anchor"] = anchor
    return result.validate()


def conservative_lower_bound(
    channels: dict[str, np.ndarray],
    *,
    anchor: str = "gaussian",
    standard_errors: Optional[dict[str, np.ndarray]] = None,
    z: float = 1.959963984540054,
) -> np.ndarray:
    """A defensible lower bound on the probability of the chosen side.

    Section 8: "Do not make up uncertainty bands." Two sources are combined,
    both of which are real rather than assumed:

      1. cross-channel spread   -- the minimum any channel assigns to the side
                                   the anchor would take;
      2. sampling error         -- where a channel reports one (Monte Carlo and
                                   the bootstrap do; closed forms do not).

    The bound is the smaller of the two, so it never claims more confidence
    than the least favourable defensible reading.
    """
    names = list(channels)
    anchor_p = np.asarray(channels.get(anchor, channels[names[0]]), dtype="float64")
    take_yes = anchor_p >= 0.5

    stack = np.vstack([np.asarray(channels[n], dtype="float64") for n in names])
    side_stack = np.where(take_yes[None, :], stack, 1.0 - stack)

    with np.errstate(invalid="ignore"):
        cross_channel_min = np.nanmin(side_stack, axis=0)

    bound = cross_channel_min
    if standard_errors:
        for name, se in standard_errors.items():
            if name not in channels:
                continue
            p = np.asarray(channels[name], dtype="float64")
            side_p = np.where(take_yes, p, 1.0 - p)
            candidate = side_p - z * np.asarray(se, dtype="float64")
            bound = np.fmin(bound, candidate)

    return np.clip(bound, 0.0, 1.0)


def disagreement_error_study(
    *,
    anchor_p: np.ndarray,
    outcome_yes: np.ndarray,
    disagreement: np.ndarray,
    groups: np.ndarray,
    confidence_floor: float = 0.80,
    n_quantiles: int = 4,
) -> dict:
    """Does disagreement separate winners from losers among confident calls?

    Restricted to HIGH-CONFIDENCE anchor predictions, because that is where the
    question matters: a disagreement signal that only works on coin flips is
    useless to a selective system.
    """
    from ..models.evaluation import clustered_accuracy

    anchor_p = np.asarray(anchor_p, dtype="float64")
    outcome = np.asarray(outcome_yes, dtype="int64")
    disagreement = np.asarray(disagreement, dtype="float64")
    groups = np.asarray(groups)

    confidence = np.maximum(anchor_p, 1.0 - anchor_p)
    take_yes = anchor_p >= 0.5
    won = np.where(take_yes, outcome == 1, outcome == 0).astype("float64")

    mask = (confidence >= confidence_floor) & np.isfinite(disagreement)
    if mask.sum() < 100:
        return {"status": "INSUFFICIENT SAMPLE", "n": int(mask.sum())}

    d = disagreement[mask]
    w = won[mask]
    g = groups[mask]

    out: dict = {
        "status": "OK",
        "confidence_floor": confidence_floor,
        "n": int(mask.sum()),
        "n_windows": int(len(np.unique(g))),
        "overall_accuracy": float(w.mean()),
        "mean_disagreement_winners": float(d[w == 1].mean()) if (w == 1).any() else float("nan"),
        "mean_disagreement_losers": float(d[w == 0].mean()) if (w == 0).any() else float("nan"),
    }
    out["disagreement_gap"] = out["mean_disagreement_losers"] - out["mean_disagreement_winners"]

    # Accuracy by disagreement quantile. If disagreement carries information,
    # accuracy should fall monotonically as disagreement rises.
    edges = np.quantile(d, np.linspace(0, 1, n_quantiles + 1))
    buckets = []
    for i in range(n_quantiles):
        lo, hi = edges[i], edges[i + 1]
        sel = (d >= lo) & (d <= hi) if i == n_quantiles - 1 else (d >= lo) & (d < hi)
        if sel.sum() < 30:
            continue
        interval = clustered_accuracy(w[sel], g[sel], n_boot=300)
        buckets.append({
            "quantile": i + 1,
            "disagreement_low": float(lo),
            "disagreement_high": float(hi),
            "n": int(sel.sum()),
            "n_windows": int(len(np.unique(g[sel]))),
            "accuracy": interval.point,
            "ci_low": interval.low,
            "ci_high": interval.high,
        })
    out["buckets"] = buckets

    if len(buckets) >= 2:
        first, last = buckets[0]["accuracy"], buckets[-1]["accuracy"]
        out["accuracy_drop_low_to_high"] = first - last
        # Non-overlapping clustered intervals is the honest bar for calling it
        # a real effect rather than a suggestive ordering.
        out["separates"] = bool(buckets[-1]["ci_high"] < buckets[0]["ci_low"])
    else:
        out["separates"] = False

    return out
