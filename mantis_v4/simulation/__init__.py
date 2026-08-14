"""
MANTIS V4 — Phase 5 simulation, sensitivity and fragility.

Normal-Z is the ANCHOR (Phase 4: it beat logistic, regularized logistic and
probit on Brier and log loss, and arrived already calibrated). Everything here
is a CHALLENGER or a DIAGNOSTIC and replaces nothing unless it demonstrates a
robust out-of-sample improvement.

Phase 5 answers five questions and nothing else:

    1. how sensitive is the probability to the volatility assumption?
    2. how fragile is it near the reference?
    3. do non-normal assumptions improve terminal probability estimates?
    4. can simulation give an independent estimate that agrees or challenges?
    5. does model disagreement identify trades to avoid?

Reading order:
    distributions.py   Gaussian anchor restated, Student-t, empirical CDF
    montecarlo.py      vectorized path simulation, crossing probability
    bootstrap.py       conditional historical fragment resampling
    sensitivity.py     digital d2/delta/gamma/vega/theta — DIAGNOSTICS ONLY
    fragility.py       0-100 fragility score; never alters probability
    agreement.py       cross-channel dispersion; does disagreement predict error?
    volatility.py      eight sigma estimators compared on calibration
    normality.py       heavy-tail stress test of the Gaussian assumption

Standing limitations:
    PROXY SETTLEMENT REFERENCE
    NO HISTORICAL WEBULL CONTRACT QUOTES
    CLASSIFICATION RESEARCH ONLY — NOT A PROFITABILITY BACKTEST
    LIMITED RECENT HISTORICAL REGIME
"""

from .agreement import (
    AgreementResult,
    combine_channels,
    conservative_lower_bound,
    disagreement_error_study,
)
from .bootstrap import BootstrapDiagnostics, EmpiricalBootstrapModel
from .distributions import (
    EmpiricalTerminalModel,
    TerminalEstimate,
    gaussian_terminal,
    normal_cdf,
    standardized_terminal_returns,
    student_t_terminal,
)
from .fragility import (
    BANDS,
    DEFAULT_WEIGHTS,
    FragilityScore,
    compute_fragility,
    volatility_of_volatility,
)
from .montecarlo import MonteCarloConfig, MonteCarloEngine
from .normality import (
    NormalityReport,
    analyse_normality,
    fit_student_t_df,
    volatility_clustering,
)
from .sensitivity import (
    Sensitivities,
    digital_sensitivities,
    normal_z_probability,
    scaled_sensitivities,
)
from .volatility import (
    EstimatorReport,
    VolatilityEstimator,
    build_estimators,
    mad_volatility,
    sigma_remaining_from,
)

__all__ = [
    "AgreementResult", "BANDS", "BootstrapDiagnostics", "DEFAULT_WEIGHTS",
    "EmpiricalBootstrapModel", "EmpiricalTerminalModel", "EstimatorReport",
    "FragilityScore", "MonteCarloConfig", "MonteCarloEngine",
    "NormalityReport", "Sensitivities", "TerminalEstimate",
    "VolatilityEstimator", "analyse_normality", "build_estimators",
    "combine_channels", "compute_fragility", "conservative_lower_bound",
    "digital_sensitivities", "disagreement_error_study", "fit_student_t_df",
    "gaussian_terminal", "mad_volatility", "normal_cdf",
    "normal_z_probability", "scaled_sensitivities", "sigma_remaining_from",
    "standardized_terminal_returns", "student_t_terminal",
    "volatility_clustering", "volatility_of_volatility",
]
