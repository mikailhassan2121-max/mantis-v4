"""
MANTIS V4 — Phase 4 transparent probability models.

Phase 4 section 2 permits exactly four families, and explicitly forbids deep
learning and (for now) gradient boosting:

    A. Normal-Z baseline        the benchmark to beat, fits nothing
    B. Logistic regression      unpenalised
    C. Regularized logistic     L2, and L1 for sparsity
    D. Probit                   same latent-index idea, Gaussian link

Every model here is INTERPRETABLE: each is a linear index passed through a
link function, so a coefficient vector is a complete description of what the
model believes. That is the point of Phase 4 — a model whose reasoning can be
read is one whose failure can be diagnosed.

WHY PROBIT IS IMPLEMENTED BY HAND

scikit-learn has no probit. Rather than add statsmodels for one link function,
it is fitted here by direct maximum likelihood with ``scipy.optimize``, with an
L2 ridge term for numerical stability. That is ~40 lines, has no new
dependency, and is easier to audit than a black box.

Note that probit and logit almost always agree closely on probabilities in the
middle of the range and differ mainly in the tails. Including both is a
robustness check on the link assumption, not an expectation of a big gap.

SCALING IS PART OF THE MODEL, NOT PART OF THE DATA

The scaler is fitted on TRAINING ROWS ONLY and stored inside the pipeline.
Fitting a scaler on the full dataset before splitting is one of the most common
silent leaks in applied ML: the test set's mean and variance bleed into the
training transform. ``test_scaler_fit_on_train_only`` verifies it.
"""

from __future__ import annotations

import json
import pickle
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

import numpy as np

DEFAULT_SEED = 20260813
EPSILON = 1e-9


def _normal_cdf(z: np.ndarray) -> np.ndarray:
    """Vectorised standard normal CDF via the error function."""
    from scipy.special import ndtr

    return ndtr(z)


class ProbabilityModel(ABC):
    """A model that maps a feature matrix to P(YES) in [0, 1]."""

    name: str = "model"
    requires_fit: bool = True

    @abstractmethod
    def fit(self, X: np.ndarray, y: np.ndarray, feature_names: Sequence[str]) -> "ProbabilityModel":
        ...

    @abstractmethod
    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        ...

    def coefficients(self) -> Optional[dict[str, float]]:
        return None

    def _check(self, p: np.ndarray) -> np.ndarray:
        """Every model output must be a valid probability. Enforced, not hoped."""
        p = np.asarray(p, dtype="float64")
        if not np.all(np.isfinite(p)):
            raise ValueError(f"{self.name} produced non-finite probabilities")
        if np.any(p < 0.0) or np.any(p > 1.0):
            raise ValueError(f"{self.name} produced probabilities outside [0, 1]")
        return p


# ---------------------------------------------------------------------------
# A. Normal-Z benchmark
# ---------------------------------------------------------------------------

class NormalZModel(ProbabilityModel):
    """The Phase 3 winner, carried forward as the benchmark to beat.

        p_yes = Phi(buffer_pct / sigma_remaining)

    It fits NOTHING. There are no parameters, so it cannot overfit, and its
    out-of-sample number is its in-sample number. That is exactly what makes it
    the right benchmark: any fitted model must beat a rule that had no
    opportunity to cheat.

    Master prompt section 26D: this is a BASELINE, never "true probability".
    """

    name = "A_normal_z"
    requires_fit = False

    def __init__(self, z_column: str = "normal_z") -> None:
        self.z_column = z_column
        self._z_index: Optional[int] = None

    def fit(self, X, y, feature_names) -> "NormalZModel":
        names = list(feature_names)
        if self.z_column not in names:
            raise ValueError(
                f"NormalZModel requires the '{self.z_column}' feature to be present"
            )
        self._z_index = names.index(self.z_column)
        return self

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        if self._z_index is None:
            raise RuntimeError("NormalZModel.fit must be called to locate the z column")
        z = np.asarray(X, dtype="float64")[:, self._z_index]
        return self._check(_normal_cdf(z))


# ---------------------------------------------------------------------------
# Shared linear scaffolding
# ---------------------------------------------------------------------------

@dataclass
class StandardScaler:
    """Mean/scale standardisation, fitted on training rows only."""

    mean_: Optional[np.ndarray] = None
    scale_: Optional[np.ndarray] = None

    def fit(self, X: np.ndarray) -> "StandardScaler":
        X = np.asarray(X, dtype="float64")
        self.mean_ = X.mean(axis=0)
        scale = X.std(axis=0, ddof=0)
        # A constant column has zero variance; leave it alone rather than
        # dividing by zero and manufacturing infinities.
        scale[scale < EPSILON] = 1.0
        self.scale_ = scale
        return self

    def transform(self, X: np.ndarray) -> np.ndarray:
        if self.mean_ is None or self.scale_ is None:
            raise RuntimeError("StandardScaler used before fit")
        return (np.asarray(X, dtype="float64") - self.mean_) / self.scale_


class LinearProbabilityModel(ProbabilityModel):
    """Common base for logit/probit: standardise, then a linear index + link."""

    link: str = "logit"

    def __init__(self, name: str, *, seed: int = DEFAULT_SEED) -> None:
        self.name = name
        self.seed = seed
        self.scaler = StandardScaler()
        self.coef_: Optional[np.ndarray] = None
        self.intercept_: float = 0.0
        self.feature_names_: list[str] = []
        self.converged_: Optional[bool] = None

    def coefficients(self) -> Optional[dict[str, float]]:
        if self.coef_ is None:
            return None
        out = {"(intercept)": float(self.intercept_)}
        out.update({n: float(c) for n, c in zip(self.feature_names_, self.coef_)})
        return out

    def top_coefficients(self, k: int = 10) -> list[tuple[str, float]]:
        coefs = self.coefficients() or {}
        ranked = sorted(
            ((n, v) for n, v in coefs.items() if n != "(intercept)"),
            key=lambda item: abs(item[1]),
            reverse=True,
        )
        return ranked[:k]


class LogisticModel(LinearProbabilityModel):
    """Logistic regression. ``penalty=None`` gives the unpenalised variant."""

    def __init__(
        self,
        name: str = "B_logistic",
        *,
        penalty: Optional[str] = None,
        C: float = 1.0,
        max_iter: int = 2000,
        seed: int = DEFAULT_SEED,
    ) -> None:
        super().__init__(name, seed=seed)
        self.penalty = penalty
        self.C = C
        self.max_iter = max_iter
        self._model = None

    def fit(self, X, y, feature_names) -> "LogisticModel":
        import warnings

        from sklearn.linear_model import LogisticRegression

        self.feature_names_ = list(feature_names)
        Xs = self.scaler.fit(X).transform(X)

        solver = "liblinear" if self.penalty == "l1" else "lbfgs"
        # scikit-learn 1.8 deprecated the `penalty` argument in favour of
        # `l1_ratio`. requirements.txt allows sklearn >= 1.4, where `l1_ratio`
        # does not exist for this estimator, so `penalty` is kept for
        # compatibility across the supported range and the (purely
        # informational) FutureWarning is suppressed to keep run output legible.
        # Revisit when the requirements floor moves past 1.8.
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=FutureWarning)
            self._model = LogisticRegression(
                penalty=self.penalty,
                C=self.C,
                solver=solver,
                max_iter=self.max_iter,
                random_state=self.seed,
            )
            self._model.fit(Xs, np.asarray(y, dtype="int64"))
        self.coef_ = self._model.coef_.ravel()
        self.intercept_ = float(self._model.intercept_[0])
        n_iter = getattr(self._model, "n_iter_", None)
        self.converged_ = bool(n_iter is not None and np.all(n_iter < self.max_iter))
        return self

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        if self._model is None:
            raise RuntimeError(f"{self.name} used before fit")
        Xs = self.scaler.transform(X)
        return self._check(self._model.predict_proba(Xs)[:, 1])


class ProbitModel(LinearProbabilityModel):
    """Probit by direct maximum likelihood (scipy), with an L2 ridge term.

    The ridge is small and exists for numerical stability on collinear
    features, not as a tuned hyperparameter.
    """

    link = "probit"

    def __init__(
        self,
        name: str = "D_probit",
        *,
        ridge: float = 1e-4,
        max_iter: int = 500,
        seed: int = DEFAULT_SEED,
    ) -> None:
        super().__init__(name, seed=seed)
        self.ridge = ridge
        self.max_iter = max_iter

    def fit(self, X, y, feature_names) -> "ProbitModel":
        from scipy.optimize import minimize
        from scipy.special import ndtr
        from scipy.stats import norm

        self.feature_names_ = list(feature_names)
        Xs = self.scaler.fit(X).transform(X)
        y = np.asarray(y, dtype="float64")

        n, k = Xs.shape
        design = np.hstack([np.ones((n, 1)), Xs])

        def negative_log_likelihood(beta):
            index = design @ beta
            p = np.clip(ndtr(index), 1e-12, 1 - 1e-12)
            ll = y * np.log(p) + (1 - y) * np.log(1 - p)
            return -ll.sum() / n + self.ridge * np.dot(beta[1:], beta[1:])

        def gradient(beta):
            index = design @ beta
            p = np.clip(ndtr(index), 1e-12, 1 - 1e-12)
            pdf = norm.pdf(index)
            weight = pdf * (y - p) / (p * (1 - p))
            grad = -(design * weight[:, None]).sum(axis=0) / n
            grad[1:] += 2 * self.ridge * beta[1:]
            return grad

        result = minimize(
            negative_log_likelihood,
            np.zeros(k + 1),
            jac=gradient,
            method="L-BFGS-B",
            options={"maxiter": self.max_iter},
        )
        self.intercept_ = float(result.x[0])
        self.coef_ = result.x[1:]
        self.converged_ = bool(result.success)
        return self

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        if self.coef_ is None:
            raise RuntimeError(f"{self.name} used before fit")
        Xs = self.scaler.transform(X)
        index = self.intercept_ + Xs @ self.coef_
        return self._check(_normal_cdf(index))


# ---------------------------------------------------------------------------

def default_models(seed: int = DEFAULT_SEED) -> list[ProbabilityModel]:
    """The Phase 4 candidate set, in the order section 2 lists them."""
    return [
        NormalZModel(),
        LogisticModel("B_logistic", penalty=None, seed=seed),
        LogisticModel("C_logistic_l2", penalty="l2", C=1.0, seed=seed),
        LogisticModel("C_logistic_l1", penalty="l1", C=0.5, seed=seed),
        ProbitModel("D_probit", seed=seed),
    ]


def save_model(model: ProbabilityModel, path: Path) -> Path:
    """Serialise a fitted model. Used by the reload test."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        pickle.dump(model, handle)
    return path


def load_model(path: Path) -> ProbabilityModel:
    with Path(path).open("rb") as handle:
        return pickle.load(handle)


def save_coefficients(model: ProbabilityModel, path: Path) -> Optional[Path]:
    coefs = model.coefficients()
    if coefs is None:
        return None
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(coefs, indent=2, sort_keys=True), encoding="utf-8")
    return path
