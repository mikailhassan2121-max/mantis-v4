"""
MANTIS V4 — normality / heavy-tail stress test (Phase 5 section 9).

Normal-Z assumes the standardized terminal return

    (terminal - spot) / spot / sigma_remaining

is standard normal. Crypto returns are famously not normal, so the interesting
question is not *whether* the assumption is violated — it will be — but whether
the violation is large enough to matter for the probabilities MANTIS actually
produces.

Section 9 is unusually clear about the trap to avoid:

    "If Gaussian remains best calibrated despite imperfect normality, say so.
     Do not abandon a good model solely because returns are not theoretically
     normal."

That warning is well aimed. A distribution can fail every formal normality test
at n = 100,000 while still producing well-calibrated probabilities, because
tests of normality answer "is this exactly Gaussian?" (always no at scale) and
calibration answers "are the resulting probabilities right?" — which is the only
question that pays.

So this module reports BOTH: the shape statistics, and the tail behaviour at
the specific quantiles the probability model actually uses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np

# The z-levels that matter operationally: these are roughly the thresholds
# behind 80%, 90%, 95%, 97.5% and 99% confidence.
TAIL_LEVELS = (0.8416, 1.2816, 1.6449, 1.9600, 2.3263)


@dataclass
class NormalityReport:
    n: int
    mean: float
    std: float
    skew: float
    excess_kurtosis: float
    jarque_bera: float
    jarque_bera_p: float
    tail_table: list[dict] = field(default_factory=list)
    qq_points: list[dict] = field(default_factory=list)
    jump_rate_4sigma: float = float("nan")
    vol_clustering_acf1: float = float("nan")
    verdict: str = ""

    def as_dict(self) -> dict:
        return {
            "n": self.n, "mean": self.mean, "std": self.std,
            "skew": self.skew, "excess_kurtosis": self.excess_kurtosis,
            "jarque_bera": self.jarque_bera, "jarque_bera_p": self.jarque_bera_p,
            "jump_rate_4sigma": self.jump_rate_4sigma,
            "vol_clustering_acf1": self.vol_clustering_acf1,
            "tail_table": self.tail_table,
            "qq_points": self.qq_points,
            "verdict": self.verdict,
        }


def analyse_normality(
    standardized: np.ndarray,
    *,
    levels: Sequence[float] = TAIL_LEVELS,
    qq_quantiles: Sequence[float] = (0.001, 0.01, 0.05, 0.10, 0.25, 0.50,
                                     0.75, 0.90, 0.95, 0.99, 0.999),
) -> NormalityReport:
    """Shape statistics plus tail behaviour at operationally relevant levels."""
    from scipy import stats

    x = np.asarray(standardized, dtype="float64")
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 100:
        return NormalityReport(
            n=n, mean=float("nan"), std=float("nan"), skew=float("nan"),
            excess_kurtosis=float("nan"), jarque_bera=float("nan"),
            jarque_bera_p=float("nan"), verdict="INSUFFICIENT SAMPLE",
        )

    mean = float(x.mean())
    std = float(x.std(ddof=1))
    skew = float(stats.skew(x))
    excess_kurtosis = float(stats.kurtosis(x))       # Fisher: 0 for a normal
    jb, jb_p = stats.jarque_bera(x)

    # THE TABLE THAT ACTUALLY MATTERS.
    # For each operational z-level, compare the Gaussian tail probability with
    # the observed one. A model that says "2% chance" while the market delivers
    # 4% is understating risk by 2x regardless of what a normality test says.
    tail_table = []
    for level in levels:
        predicted = float(stats.norm.sf(level))          # one-sided
        observed_upper = float((x > level).mean())
        observed_lower = float((x < -level).mean())
        observed = 0.5 * (observed_upper + observed_lower)
        tail_table.append({
            "z": level,
            "gaussian_tail": predicted,
            "observed_upper": observed_upper,
            "observed_lower": observed_lower,
            "observed_mean": observed,
            "ratio_observed_over_gaussian": (
                observed / predicted if predicted > 0 else float("nan")
            ),
        })

    qq_points = []
    for q in qq_quantiles:
        qq_points.append({
            "quantile": q,
            "empirical": float(np.quantile(x, q)),
            "gaussian": float(stats.norm.ppf(q)),
        })

    jump_rate = float((np.abs(x) > 4.0).mean())

    report = NormalityReport(
        n=n, mean=mean, std=std, skew=skew, excess_kurtosis=excess_kurtosis,
        jarque_bera=float(jb), jarque_bera_p=float(jb_p),
        tail_table=tail_table, qq_points=qq_points, jump_rate_4sigma=jump_rate,
    )

    # A verdict that talks about MAGNITUDE, not statistical significance. At
    # n > 100,000 every real distribution rejects normality; that fact alone
    # says nothing about whether the probabilities are usable.
    worst_ratio = max(
        (row["ratio_observed_over_gaussian"] for row in tail_table
         if np.isfinite(row["ratio_observed_over_gaussian"])),
        default=float("nan"),
    )
    if not np.isfinite(worst_ratio):
        report.verdict = "undetermined"
    elif worst_ratio > 2.0:
        report.verdict = f"HEAVY TAILS — observed up to {worst_ratio:.1f}x Gaussian"
    elif worst_ratio > 1.3:
        report.verdict = f"moderately heavy tails — up to {worst_ratio:.1f}x Gaussian"
    elif worst_ratio < 0.7:
        report.verdict = f"THIN TAILS — observed only {worst_ratio:.1f}x Gaussian"
    else:
        report.verdict = "tails close to Gaussian at operational levels"
    return report


def volatility_clustering(returns: np.ndarray, lag: int = 1) -> float:
    """Autocorrelation of |returns| — the standard clustering signature.

    Positive values mean volatile minutes follow volatile minutes, which is why
    a trailing sigma estimate has any predictive value at all.
    """
    r = np.asarray(returns, dtype="float64")
    r = r[np.isfinite(r)]
    if len(r) < lag + 30:
        return float("nan")
    a = np.abs(r)
    a = a - a.mean()
    denominator = float(np.dot(a, a))
    if denominator <= 0:
        return float("nan")
    return float(np.dot(a[:-lag], a[lag:]) / denominator)


def fit_student_t_df(standardized: np.ndarray, *, bounds: tuple = (2.1, 60.0)) -> dict:
    """Maximum-likelihood degrees of freedom for a Student-t fit.

    A low nu means heavy tails; nu above ~30 is indistinguishable from Gaussian
    for practical purposes. Reported so the Student-t challenger's parameter is
    estimated from data rather than picked by hand.
    """
    from scipy import stats

    x = np.asarray(standardized, dtype="float64")
    x = x[np.isfinite(x)]
    if len(x) < 500:
        return {"status": "INSUFFICIENT SAMPLE", "n": len(x)}

    df, loc, scale = stats.t.fit(x)
    df = float(np.clip(df, *bounds))
    return {
        "status": "OK",
        "n": len(x),
        "df": df,
        "loc": float(loc),
        "scale": float(scale),
        "interpretation": (
            "indistinguishable from Gaussian" if df > 30
            else "moderately heavy" if df > 8
            else "heavy tailed"
        ),
    }
