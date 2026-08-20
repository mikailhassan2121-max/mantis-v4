"""Calibration reporting interfaces; live probabilities remain unchanged."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Protocol


@dataclass(frozen=True)
class CalibratedProbability:
    point: float
    conservative: float
    method: str
    sample_size: int = 0


class ProbabilityCalibrator(Protocol):
    def calibrate(self, point: float, conservative: float) -> CalibratedProbability: ...


class IdentityCalibrator:
    """Preserves specialist probabilities until real calibration is validated."""
    method = "IDENTITY_UNCALIBRATED"

    def calibrate(self, point: float, conservative: float) -> CalibratedProbability:
        if not 0 <= point <= 1 or not 0 <= conservative <= 1:
            raise ValueError("probabilities must be in [0, 1]")
        return CalibratedProbability(float(point), float(conservative), self.method, 0)


@dataclass(frozen=True)
class CalibrationObservation:
    probability: float
    outcome: bool
    agent: str
    policy_version: str

    def __post_init__(self) -> None:
        if not 0 <= self.probability <= 1:
            raise ValueError("probability must be in [0, 1]")


@dataclass(frozen=True)
class CalibrationReport:
    sample_size: int
    brier_score: float | None
    log_loss: float | None
    expected_calibration_error: float | None
    bins: tuple[dict, ...]
    status: str


def evaluate_calibration(observations: list[CalibrationObservation], *,
                         bin_count: int = 10, minimum_sample: int = 30) -> CalibrationReport:
    """Score accumulated resolved forecasts; never fits or activates a model."""
    if bin_count < 1:
        raise ValueError("bin_count must be positive")
    if not observations:
        return CalibrationReport(0, None, None, None, (), "INSUFFICIENT_EVIDENCE")
    count = len(observations)
    brier = sum((row.probability - float(row.outcome)) ** 2 for row in observations) / count
    epsilon = 1e-15
    log_loss = -sum(float(row.outcome) * math.log(max(epsilon, row.probability)) +
                    (1.0 - float(row.outcome)) * math.log(max(epsilon, 1.0 - row.probability))
                    for row in observations) / count
    bins = []
    weighted_error = 0.0
    for index in range(bin_count):
        lower = index / bin_count
        upper = (index + 1) / bin_count
        members = [row for row in observations
                   if lower <= row.probability < upper or (index == bin_count - 1 and row.probability == 1)]
        if not members:
            continue
        mean_probability = sum(row.probability for row in members) / len(members)
        observed_rate = sum(float(row.outcome) for row in members) / len(members)
        weighted_error += len(members) / count * abs(mean_probability - observed_rate)
        bins.append({"lower": lower, "upper": upper, "count": len(members),
                     "mean_probability": mean_probability, "observed_rate": observed_rate})
    status = "REPORT_ONLY" if count >= minimum_sample else "INSUFFICIENT_EVIDENCE"
    return CalibrationReport(count, brier, log_loss, weighted_error, tuple(bins), status)
