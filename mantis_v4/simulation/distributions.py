"""
MANTIS V4 — terminal-distribution models (Phase 5 section 2).

Normal-Z is the ANCHOR. Phase 4 established it as the production probability
model: it beat logistic, L2/L1-regularized logistic and probit on Brier and log
loss, and it arrived already calibrated (holdout slope 1.011, ECE 0.0098). It
has zero fitted parameters and therefore cannot overfit.

Everything in this module is a CHALLENGER or a DIAGNOSTIC. Nothing here
replaces Normal-Z unless it demonstrates a robust out-of-sample improvement,
and Phase 5 section 1 is explicit that a worse model may be kept only if it is
useful as a diagnostic.

THE MODELS

  A. Gaussian        the anchor's own assumption, restated so the comparison
                     is like-for-like
  B. Student-t       same location/scale, heavier tails
  C. Empirical       the observed conditional distribution of remaining
                     returns, no parametric assumption at all
  D. Bootstrap       resampled historical fragments (see bootstrap.py)

A NOTE ON RETURN CONVENTIONS

Normal-Z as validated in Phases 3-4 uses the SIMPLE return buffer:

    z = (spot - reference) / reference / sigma_remaining_return

A log-return formulation would use log(spot/reference) instead. Over a
15-minute crypto window the buffer is a fraction of a percent, where
log(1+x) ~ x to better than 1 part in 10,000, so the two agree to far beyond
any precision this dataset supports.

The anchor's exact formulation is preserved unchanged (section 1: "Do not
modify its validated formulation unless a bug is proven"). Challengers are
built on the same simple-return convention so that any measured difference
comes from the DISTRIBUTIONAL assumption and not from a units change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np

EPSILON = 1e-12
MIN_SIGMA = 1e-9


@dataclass
class TerminalEstimate:
    """One model's view of the terminal outcome for a set of rows."""

    name: str
    p_yes: np.ndarray
    quantile_05: Optional[np.ndarray] = None
    quantile_50: Optional[np.ndarray] = None
    quantile_95: Optional[np.ndarray] = None
    expected_terminal_buffer: Optional[np.ndarray] = None
    p_cross_reference: Optional[np.ndarray] = None
    standard_error: Optional[np.ndarray] = None
    sample_size: Optional[np.ndarray] = None
    notes: str = ""
    diagnostics: dict = field(default_factory=dict)

    @property
    def p_no(self) -> np.ndarray:
        return 1.0 - self.p_yes

    def validate(self) -> "TerminalEstimate":
        """Probabilities must be finite and inside [0, 1]. Enforced, not hoped."""
        p = np.asarray(self.p_yes, dtype="float64")
        if not np.all(np.isfinite(p)):
            raise ValueError(f"{self.name}: non-finite probability produced")
        if np.any(p < 0.0) or np.any(p > 1.0):
            raise ValueError(f"{self.name}: probability outside [0, 1]")
        return self

    def lower_confidence_bound(self, z: float = 1.959963984540054) -> Optional[np.ndarray]:
        """Conservative bound on the probability of the side being taken.

        Phase 5 section 8: do not make up uncertainty bands. This returns None
        when the model has no defensible sampling-uncertainty estimate, rather
        than inventing one.
        """
        if self.standard_error is None:
            return None
        p = np.asarray(self.p_yes, dtype="float64")
        se = np.asarray(self.standard_error, dtype="float64")
        confidence = np.where(p >= 0.5, p, 1.0 - p)
        return np.clip(confidence - z * se, 0.0, 1.0)


def normal_cdf(x: np.ndarray) -> np.ndarray:
    from scipy.special import ndtr

    return ndtr(np.asarray(x, dtype="float64"))


def normal_pdf(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype="float64")
    return np.exp(-0.5 * x * x) / np.sqrt(2.0 * np.pi)


# ---------------------------------------------------------------------------
# A. Gaussian (the anchor, restated)
# ---------------------------------------------------------------------------

def gaussian_terminal(
    buffer_pct: np.ndarray,
    sigma_remaining: np.ndarray,
    *,
    spot: Optional[np.ndarray] = None,
    reference: Optional[np.ndarray] = None,
    name: str = "gaussian",
) -> TerminalEstimate:
    """P(YES) = Phi(buffer / sigma_remaining) — Normal-Z's own assumption.

    Restated here so that Student-t and the empirical models are compared
    against an identically-constructed Gaussian rather than against a
    differently-plumbed reimplementation.
    """
    buffer_pct = np.asarray(buffer_pct, dtype="float64")
    sigma = np.maximum(np.asarray(sigma_remaining, dtype="float64"), MIN_SIGMA)

    z = buffer_pct / sigma
    p_yes = normal_cdf(z)

    estimate = TerminalEstimate(name=name, p_yes=p_yes)

    if spot is not None and reference is not None:
        spot = np.asarray(spot, dtype="float64")
        # Terminal price quantiles under the same Gaussian assumption.
        from scipy.stats import norm

        for q, attr in ((0.05, "quantile_05"), (0.50, "quantile_50"), (0.95, "quantile_95")):
            setattr(estimate, attr, spot * (1.0 + norm.ppf(q) * sigma))
        # Driftless: expected terminal buffer is the current buffer.
        estimate.expected_terminal_buffer = buffer_pct
        # Reflection principle for driftless Brownian motion: the probability
        # of touching the reference before expiry is twice the probability of
        # finishing beyond it, capped at 1.
        estimate.p_cross_reference = np.minimum(1.0, 2.0 * normal_cdf(-np.abs(z)))

    return estimate.validate()


# ---------------------------------------------------------------------------
# B. Student-t
# ---------------------------------------------------------------------------

def student_t_terminal(
    buffer_pct: np.ndarray,
    sigma_remaining: np.ndarray,
    *,
    nu: float = 4.0,
    spot: Optional[np.ndarray] = None,
    name: Optional[str] = None,
) -> TerminalEstimate:
    """Student-t terminal returns with the SAME variance as the Gaussian.

    The scale is set so that the t distribution's variance equals
    ``sigma_remaining ** 2``:

        Var(t_nu) = nu / (nu - 2)   =>   scale = sigma * sqrt((nu - 2) / nu)

    Without that correction a t model would simply be a wider Gaussian and any
    "improvement" would be a volatility change wearing a distributional
    disguise. Matching variance isolates the effect of TAIL SHAPE, which is the
    thing Phase 5 section 9 actually wants to test.

    Requires nu > 2 for a finite variance.
    """
    from scipy.stats import t as student_t

    if nu <= 2.0:
        raise ValueError("nu must exceed 2 for a finite-variance Student-t")

    name = name or f"student_t_nu{nu:g}"
    buffer_pct = np.asarray(buffer_pct, dtype="float64")
    sigma = np.maximum(np.asarray(sigma_remaining, dtype="float64"), MIN_SIGMA)

    scale = sigma * np.sqrt((nu - 2.0) / nu)
    p_yes = student_t.cdf(buffer_pct / scale, df=nu)

    estimate = TerminalEstimate(name=name, p_yes=np.asarray(p_yes, dtype="float64"))
    estimate.diagnostics["nu"] = nu

    if spot is not None:
        spot = np.asarray(spot, dtype="float64")
        for q, attr in ((0.05, "quantile_05"), (0.50, "quantile_50"), (0.95, "quantile_95")):
            setattr(estimate, attr, spot * (1.0 + student_t.ppf(q, df=nu) * scale))
        estimate.expected_terminal_buffer = buffer_pct

    return estimate.validate()


# ---------------------------------------------------------------------------
# C. Empirical conditional distribution
# ---------------------------------------------------------------------------

class EmpiricalTerminalModel:
    """The observed conditional distribution of standardized remaining returns.

    No parametric assumption. For each horizon bucket we hold the empirical
    distribution of

        (terminal_return) / sigma_remaining

    observed in TRAINING data only, and read P(YES) straight off it:

        P(YES) = fraction of standardized outcomes exceeding -z

    Standardizing by sigma_remaining is what makes fragments from different
    volatility regimes comparable. Without it the pooled distribution would be
    a mixture dominated by whichever regime was most common in the sample.

    FITTED ON TRAINING DATA ONLY. ``fit`` takes the training rows explicitly;
    there is no path that reads the evaluation rows.
    """

    name = "empirical_conditional"

    def __init__(self, horizon_buckets: Sequence[float] = (60, 150, 300, 450, 600, 901),
                 min_samples: int = 200) -> None:
        self.horizon_buckets = tuple(horizon_buckets)
        self.min_samples = min_samples
        self._pools: dict[int, np.ndarray] = {}
        self._global_pool: Optional[np.ndarray] = None

    def _bucket_of(self, seconds_remaining: np.ndarray) -> np.ndarray:
        return np.searchsorted(np.asarray(self.horizon_buckets), seconds_remaining, side="left")

    def fit(
        self,
        standardized_returns: np.ndarray,
        seconds_remaining: np.ndarray,
    ) -> "EmpiricalTerminalModel":
        standardized_returns = np.asarray(standardized_returns, dtype="float64")
        seconds_remaining = np.asarray(seconds_remaining, dtype="float64")

        finite = np.isfinite(standardized_returns)
        standardized_returns = standardized_returns[finite]
        seconds_remaining = seconds_remaining[finite]

        self._global_pool = np.sort(standardized_returns)
        buckets = self._bucket_of(seconds_remaining)
        for bucket in np.unique(buckets):
            pool = standardized_returns[buckets == bucket]
            if len(pool) >= self.min_samples:
                self._pools[int(bucket)] = np.sort(pool)
        return self

    def predict(
        self,
        buffer_pct: np.ndarray,
        sigma_remaining: np.ndarray,
        seconds_remaining: np.ndarray,
    ) -> TerminalEstimate:
        if self._global_pool is None:
            raise RuntimeError("EmpiricalTerminalModel used before fit")

        buffer_pct = np.asarray(buffer_pct, dtype="float64")
        sigma = np.maximum(np.asarray(sigma_remaining, dtype="float64"), MIN_SIGMA)
        z = buffer_pct / sigma

        buckets = self._bucket_of(np.asarray(seconds_remaining, dtype="float64"))
        p_yes = np.empty(len(z), dtype="float64")
        sample_size = np.empty(len(z), dtype="float64")

        for bucket in np.unique(buckets):
            mask = buckets == bucket
            pool = self._pools.get(int(bucket), self._global_pool)
            # P(YES) = P(terminal standardized return > -z)
            #        = 1 - F(-z), read off the empirical CDF.
            position = np.searchsorted(pool, -z[mask], side="left")
            p_yes[mask] = 1.0 - position / len(pool)
            sample_size[mask] = len(pool)

        # Clip away from the hard 0/1 the empirical CDF produces past its
        # support. A finite sample cannot justify certainty, and log loss is
        # infinite at a confidently wrong 0 or 1.
        floor = 1.0 / (2.0 * sample_size)
        p_yes = np.clip(p_yes, floor, 1.0 - floor)

        estimate = TerminalEstimate(
            name=self.name, p_yes=p_yes, sample_size=sample_size,
            notes="empirical conditional CDF of standardized remaining returns",
        )
        # Binomial sampling error of an empirical quantile estimate.
        estimate.standard_error = np.sqrt(
            np.clip(p_yes * (1.0 - p_yes), 0.0, None) / sample_size
        )
        return estimate.validate()


def standardized_terminal_returns(
    spot: np.ndarray,
    terminal_price: np.ndarray,
    sigma_remaining: np.ndarray,
) -> np.ndarray:
    """(terminal - spot)/spot divided by sigma_remaining.

    This is the quantity Normal-Z implicitly assumes is standard normal, so it
    is also the quantity the normality stress test examines.
    """
    spot = np.asarray(spot, dtype="float64")
    terminal_price = np.asarray(terminal_price, dtype="float64")
    sigma = np.maximum(np.asarray(sigma_remaining, dtype="float64"), MIN_SIGMA)
    return ((terminal_price - spot) / spot) / sigma
