"""
MANTIS V4 — prediction metrics (Phase 3 requirements 12 and 13).

REQUIREMENT 13 IS ENFORCED IN CODE, NOT JUST IN PROSE.

    CLASSIFICATION ACCURACY  is not  ECONOMIC PROFITABILITY.

``StrategyMetrics`` has no P&L field, no EV field, and no return field. There is
nothing for a reader to mistake for profitability, because the arithmetic that
would produce it does not exist in this module. A strategy that takes only
contracts priced at $0.95 can be 94% accurate and lose money on every trade;
accuracy alone cannot distinguish that from a genuinely profitable rule.

Economic evaluation requires historical executable contract prices. This
dataset has none (Phase 2 audit UNKNOWN-4/5), so profitability is reported as
NOT EVALUABLE and nothing is approximated.

PROBABILITY METRICS ARE GATED (requirement 12)

Brier score and log loss are computed ONLY from genuine probability outputs.
A strategy that emits no probability gets ``None``, not a substituted 0.5 or a
0/1 hard label dressed up as a probability. Substituting either would produce a
number that looks like a calibration measurement and is an artefact.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

# z for a two-sided 95% interval.
Z_95 = 1.959963984540054


def wilson_interval(
    successes: int, trials: int, z: float = Z_95
) -> tuple[Optional[float], Optional[float]]:
    """Wilson score interval for a binomial proportion.

    Master prompt section 16 requires an interval on every accuracy claim.
    Wilson rather than the normal approximation because it stays inside [0, 1]
    and behaves sanely at small n — which matters enormously here, where a
    per-bucket cell may hold a handful of contracts.
    """
    if trials <= 0:
        return None, None
    p = successes / trials
    denominator = 1.0 + z * z / trials
    centre = (p + z * z / (2 * trials)) / denominator
    margin = (
        z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials))
    ) / denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


def brier_score(probabilities: Sequence[float], outcomes: Sequence[int]) -> Optional[float]:
    """Mean squared error of probabilistic forecasts. Lower is better.

    A constant 0.5 forecast scores 0.25, which is the reference point any
    genuine model must beat.
    """
    pairs = [
        (p, o) for p, o in zip(probabilities, outcomes)
        if p is not None and o is not None and p == p
    ]
    if not pairs:
        return None
    return sum((p - o) ** 2 for p, o in pairs) / len(pairs)


def log_loss(
    probabilities: Sequence[float], outcomes: Sequence[int], epsilon: float = 1e-15
) -> Optional[float]:
    """Negative log likelihood. Clipped so a confident miss is finite.

    The clip is disclosed rather than silent: without it a single p=0.0 forecast
    on a YES outcome makes the whole metric infinite, which hides everything
    else. With it, such a forecast is heavily but finitely penalised.
    """
    pairs = [
        (p, o) for p, o in zip(probabilities, outcomes)
        if p is not None and o is not None and p == p
    ]
    if not pairs:
        return None
    total = 0.0
    for p, o in pairs:
        clipped = min(1 - epsilon, max(epsilon, p))
        total += -(o * math.log(clipped) + (1 - o) * math.log(1 - clipped))
    return total / len(pairs)


@dataclass
class StrategyMetrics:
    """Classification metrics for one strategy over one slice of contracts.

    Deliberately contains NO economic field. See the module docstring.
    """

    label: str = "all"
    contracts_observed: int = 0        # usable contracts this slice covers
    contracts_traded: int = 0
    correct: int = 0
    incorrect: int = 0

    yes_entries: int = 0
    no_entries: int = 0
    yes_outcomes: int = 0

    brier: Optional[float] = None
    logloss: Optional[float] = None
    mean_p_for_side: Optional[float] = None

    probabilities_available: bool = False

    @property
    def coverage(self) -> Optional[float]:
        """Fraction of observed contracts actually traded."""
        if self.contracts_observed <= 0:
            return None
        return self.contracts_traded / self.contracts_observed

    @property
    def abstention_rate(self) -> Optional[float]:
        coverage = self.coverage
        return None if coverage is None else 1.0 - coverage

    @property
    def accuracy(self) -> Optional[float]:
        scored = self.correct + self.incorrect
        if scored <= 0:
            return None
        return self.correct / scored

    @property
    def accuracy_ci(self) -> tuple[Optional[float], Optional[float]]:
        return wilson_interval(self.correct, self.correct + self.incorrect)

    @property
    def base_rate_yes(self) -> Optional[float]:
        """How often the contract settled YES in this slice.

        The honest comparison point for accuracy: a market drifting up makes
        "always YES" look skilful.
        """
        if self.contracts_observed <= 0:
            return None
        return self.yes_outcomes / self.contracts_observed

    def as_row(self) -> dict:
        low, high = self.accuracy_ci
        return {
            "label": self.label,
            "contracts_observed": self.contracts_observed,
            "contracts_traded": self.contracts_traded,
            "coverage": self.coverage,
            "abstention_rate": self.abstention_rate,
            "correct": self.correct,
            "incorrect": self.incorrect,
            "accuracy": self.accuracy,
            "accuracy_ci_low": low,
            "accuracy_ci_high": high,
            "base_rate_yes": self.base_rate_yes,
            "yes_entries": self.yes_entries,
            "no_entries": self.no_entries,
            "brier": self.brier,
            "logloss": self.logloss,
            "mean_p_for_side": self.mean_p_for_side,
            "probabilities_available": self.probabilities_available,
            "economic_profitability": "NOT EVALUABLE - no historical contract prices",
        }


def evaluate(replays: Iterable, label: str = "all") -> StrategyMetrics:
    """Compute classification metrics over a set of ``ContractReplay`` objects.

    Only contracts that actually settled (``usable``) are counted. An
    unresolvable contract is neither a win nor a loss and must not silently
    inflate or deflate coverage.
    """
    metrics = StrategyMetrics(label=label)
    probabilities: list[float] = []
    outcomes: list[int] = []
    side_probabilities: list[float] = []

    for replay in replays:
        if not getattr(replay, "usable", False) or replay.outcome_yes is None:
            continue

        metrics.contracts_observed += 1
        if replay.outcome_yes:
            metrics.yes_outcomes += 1

        # Brier/log loss use every scored forecast, traded or not, provided a
        # genuine probability exists. Restricting them to traded contracts only
        # would measure calibration on a self-selected subset.
        if replay.entry_p_yes is not None and replay.entry_p_yes == replay.entry_p_yes:
            probabilities.append(float(replay.entry_p_yes))
            outcomes.append(1 if replay.outcome_yes else 0)

        if not replay.entered or replay.entry_side is None:
            continue

        metrics.contracts_traded += 1
        if replay.entry_side == "YES":
            metrics.yes_entries += 1
        else:
            metrics.no_entries += 1

        if replay.prediction_correct == 1:
            metrics.correct += 1
        elif replay.prediction_correct == 0:
            metrics.incorrect += 1

        if replay.entry_p_yes is not None and replay.entry_p_yes == replay.entry_p_yes:
            p = float(replay.entry_p_yes)
            side_probabilities.append(p if replay.entry_side == "YES" else 1.0 - p)

    if probabilities:
        metrics.probabilities_available = True
        metrics.brier = brier_score(probabilities, outcomes)
        metrics.logloss = log_loss(probabilities, outcomes)
    if side_probabilities:
        metrics.mean_p_for_side = sum(side_probabilities) / len(side_probabilities)

    return metrics


# ---------------------------------------------------------------------------
# Bucketing (requirement 12: per-asset, direction, entry time, volatility)
# ---------------------------------------------------------------------------

def entry_time_bucket(replay) -> str:
    """Which part of the contract the entry landed in."""
    if not replay.entered or replay.entry_timestamp is None:
        return "no_entry"
    remaining = replay.window.seconds_remaining(replay.entry_timestamp)
    if remaining > 600:
        return "T-900..600s"
    if remaining > 450:
        return "T-600..450s"
    if remaining > 300:
        return "T-450..300s"
    if remaining > 150:
        return "T-300..150s"
    return "T-150..0s"


def volatility_bucket(replay, edges: Sequence[float]) -> str:
    """Realised-volatility tercile at entry, using dataset-wide edges."""
    features = replay.entry_features
    if not features:
        return "no_entry"
    vol = features.get("realized_vol_1m")
    if vol is None or vol != vol:
        return "unknown"
    for i, edge in enumerate(edges):
        if vol <= edge:
            return f"vol_q{i + 1}"
    return f"vol_q{len(edges) + 1}"


def quantile_edges(values: Sequence[float], quantiles: Sequence[float] = (1 / 3, 2 / 3)) -> list[float]:
    clean = sorted(v for v in values if v is not None and v == v)
    if not clean:
        return []
    edges = []
    for q in quantiles:
        index = min(len(clean) - 1, max(0, int(q * len(clean))))
        edges.append(clean[index])
    return edges


def group_by(replays: Sequence, key) -> dict[str, list]:
    groups: dict[str, list] = {}
    for replay in replays:
        groups.setdefault(str(key(replay)), []).append(replay)
    return groups


@dataclass
class StrategyReport:
    """Everything measured for one strategy."""

    strategy: str
    evaluable: bool = True
    not_evaluable_reason: str = ""
    overall: Optional[StrategyMetrics] = None
    by_asset: dict[str, StrategyMetrics] = field(default_factory=dict)
    by_direction: dict[str, StrategyMetrics] = field(default_factory=dict)
    by_entry_time: dict[str, StrategyMetrics] = field(default_factory=dict)
    by_volatility: dict[str, StrategyMetrics] = field(default_factory=dict)
    min_buffer_z_binding_count: Optional[int] = None


def build_report(strategy_name: str, replays: Sequence) -> StrategyReport:
    """Overall plus every required breakdown."""
    report = StrategyReport(strategy=strategy_name)
    report.overall = evaluate(replays, "overall")

    for asset, group in sorted(group_by(replays, lambda r: r.asset).items()):
        report.by_asset[asset] = evaluate(group, asset)

    traded = [r for r in replays if r.entered and getattr(r, "usable", False)]
    for side, group in sorted(group_by(traded, lambda r: r.entry_side or "none").items()):
        report.by_direction[side] = evaluate(group, side)

    for bucket, group in sorted(group_by(traded, entry_time_bucket).items()):
        report.by_entry_time[bucket] = evaluate(group, bucket)

    vols = [
        r.entry_features.get("realized_vol_1m")
        for r in traded
        if r.entry_features
    ]
    edges = quantile_edges([v for v in vols if v is not None])
    if edges:
        for bucket, group in sorted(
            group_by(traded, lambda r: volatility_bucket(r, edges)).items()
        ):
            report.by_volatility[bucket] = evaluate(group, bucket)

    return report
