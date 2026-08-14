"""
MANTIS V4 — digital/binary sensitivity diagnostics (Phase 5 section 5).

READ THIS FIRST.

    THESE ARE SENSITIVITY DIAGNOSTICS, NOT DIRECTIONAL ALPHA.

Nothing in this module predicts YES or NO. Master prompt section 20 forbids
"pretend Greeks imply direction", and Phase 5 section 5 repeats it. What these
quantities measure is how violently the probability estimate MOVES when its
inputs move — which is a statement about the estimate's stability, not about
the market's direction.

The useful readings are:

    high |gamma| near the reference close to expiry
        the probability is hypersensitive to spot; a tick either way flips it.
        FRAGILE.

    high |vega|
        the probability is largely a restatement of the volatility estimate.
        Since sigma is itself estimated from ~20-60 noisy bars, a high-vega
        state means the "probability" inherits that estimation error.

    high |theta|
        the probability is moving fast simply because time is passing. This is
        the mechanical effect Phase 4 measured: mean |z| rose from 0.387 to
        3.904 as the window drained, purely because sigma_remaining shrinks
        as sqrt(t).

MODEL FRAME

MANTIS's validated anchor is Normal-Z on SIMPLE returns:

    p = Phi(z),   z = (S - K) / K / sigma_rem

so the sensitivities are taken of THAT function, not of a textbook
Black-Scholes digital. That matters: importing Black-Scholes wholesale would
introduce a lognormal assumption, a risk-neutral measure and a discount rate
that this system does not use and cannot justify (master prompt section 26E is
explicit that risk-neutral and physical probabilities are not the same thing).

``d2`` is reported for continuity with the options literature and is computed
in the lognormal frame; it is a DIAGNOSTIC ONLY and is not what drives p.

Every analytic derivative below is verified against a central finite difference
in ``test_greeks_match_finite_differences``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

MIN_SIGMA = 1e-9
MIN_TIME = 1e-9


def _pdf(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype="float64")
    return np.exp(-0.5 * x * x) / np.sqrt(2.0 * np.pi)


def _cdf(x: np.ndarray) -> np.ndarray:
    from scipy.special import ndtr

    return ndtr(np.asarray(x, dtype="float64"))


@dataclass
class Sensitivities:
    """Partial derivatives of p = Phi((S-K)/K/sigma_rem)."""

    p_yes: np.ndarray
    z: np.ndarray
    d2: np.ndarray
    delta: np.ndarray      # dp/dS
    gamma: np.ndarray      # d2p/dS2
    vega: np.ndarray       # dp/dsigma_1m
    theta: np.ndarray      # dp/dt   (t in seconds)

    def as_dict(self) -> dict:
        return {
            "p_yes": self.p_yes, "z": self.z, "d2": self.d2,
            "delta": self.delta, "gamma": self.gamma,
            "vega": self.vega, "theta": self.theta,
        }

    def validate(self) -> "Sensitivities":
        for name, array in self.as_dict().items():
            if not np.all(np.isfinite(array)):
                raise ValueError(f"sensitivity {name} contains non-finite values")
        return self


def digital_sensitivities(
    *,
    spot: np.ndarray,
    reference: np.ndarray,
    seconds_remaining: np.ndarray,
    sigma_1m: np.ndarray,
) -> Sensitivities:
    """Analytic sensitivities of the Normal-Z probability.

    With
        T      = seconds_remaining / 60          (minutes)
        sigma  = sigma_1m * sqrt(T)              (remaining-window vol)
        z      = (S - K) / (K * sigma)
        p      = Phi(z)

    the derivatives are

        dp/dS      = phi(z) / (K * sigma)
        d2p/dS2    = -z * phi(z) / (K * sigma)^2
        dp/dsigma1 = -phi(z) * z / sigma_1m
        dp/dt      = -phi(z) * z / (2 * t)       (t in seconds)

    The vega and theta forms follow because z is proportional to
    1/sigma_1m and to 1/sqrt(t) respectively, so dz/dsigma1 = -z/sigma_1m and
    dz/dt = -z/(2t).
    """
    spot = np.asarray(spot, dtype="float64")
    reference = np.asarray(reference, dtype="float64")
    seconds = np.maximum(np.asarray(seconds_remaining, dtype="float64"), MIN_TIME)
    sigma_1m = np.maximum(np.asarray(sigma_1m, dtype="float64"), MIN_SIGMA)

    minutes = seconds / 60.0
    sigma_rem = np.maximum(sigma_1m * np.sqrt(minutes), MIN_SIGMA)

    z = (spot - reference) / (reference * sigma_rem)
    p = _cdf(z)
    phi = _pdf(z)

    denominator = reference * sigma_rem
    delta = phi / denominator
    gamma = -z * phi / (denominator**2)
    vega = -phi * z / sigma_1m
    theta = -phi * z / (2.0 * seconds)

    # d2 in the lognormal frame, reported for continuity with the options
    # literature. DIAGNOSTIC ONLY -- it does not drive p.
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(reference > 0, spot / reference, np.nan)
        d2 = np.where(
            (ratio > 0) & np.isfinite(ratio),
            (np.log(np.where(ratio > 0, ratio, 1.0)) - 0.5 * sigma_rem**2) / sigma_rem,
            0.0,
        )
    d2 = np.nan_to_num(d2, nan=0.0, posinf=0.0, neginf=0.0)

    return Sensitivities(
        p_yes=p, z=z, d2=d2, delta=delta, gamma=gamma, vega=vega, theta=theta
    ).validate()


def normal_z_probability(
    spot: np.ndarray,
    reference: np.ndarray,
    seconds_remaining: np.ndarray,
    sigma_1m: np.ndarray,
) -> np.ndarray:
    """The anchor probability, in the exact parameterisation used above.

    Exists so the finite-difference test can perturb one input at a time
    against the same function the analytic derivatives describe.
    """
    seconds = np.maximum(np.asarray(seconds_remaining, dtype="float64"), MIN_TIME)
    sigma_1m = np.maximum(np.asarray(sigma_1m, dtype="float64"), MIN_SIGMA)
    sigma_rem = np.maximum(sigma_1m * np.sqrt(seconds / 60.0), MIN_SIGMA)
    z = (np.asarray(spot, dtype="float64") - np.asarray(reference, dtype="float64")) / (
        np.asarray(reference, dtype="float64") * sigma_rem
    )
    return _cdf(z)


def scaled_sensitivities(sens: Sensitivities, spot: np.ndarray,
                         sigma_1m: np.ndarray) -> dict[str, np.ndarray]:
    """Unit-free versions, comparable across assets.

    Raw delta and gamma carry price units, so BTC at ~63,000 and ADA at ~0.18
    produce numbers five orders of magnitude apart for identical fragility.
    These rescalings answer "how much does p move for a RELATIVE input change",
    which is the question fragility actually cares about.
    """
    spot = np.asarray(spot, dtype="float64")
    sigma_1m = np.asarray(sigma_1m, dtype="float64")
    return {
        # dp per 1% move in spot
        "delta_per_pct": sens.delta * spot * 0.01,
        # curvature per (1% move)^2
        "gamma_per_pct2": sens.gamma * (spot * 0.01) ** 2,
        # dp per 10% relative change in the volatility estimate
        "vega_per_10pct_vol": sens.vega * sigma_1m * 0.10,
        # dp per 30 seconds of time passing
        "theta_per_30s": sens.theta * 30.0,
    }
