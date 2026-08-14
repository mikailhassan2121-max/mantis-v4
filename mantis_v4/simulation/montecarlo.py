"""
MANTIS V4 — vectorized Monte Carlo terminal-resolution engine (Phase 5 section 3).

WHAT MONTE CARLO IS ACTUALLY FOR HERE

For a driftless Gaussian walk, MC converges to exactly what Normal-Z already
computes in closed form. Running MC to reproduce Φ(z) to three decimal places
would be an expensive way to learn nothing.

MC earns its place on three things a closed form cannot give:

  1. PATH-DEPENDENT quantities — chiefly the probability of touching the
     reference before expiry, which matters because a contract that crosses
     repeatedly is a different object from one that drifted away and stayed.
  2. NON-GAUSSIAN innovations — bootstrapped from real standardized 1-minute
     returns, so the simulated tails are the market's tails and not an
     assumption.
  3. An INDEPENDENT probability channel whose disagreement with Normal-Z is
     itself a signal (Phase 5 section 7).

DRIFT IS AGGRESSIVELY SHRUNK

Section 3 is explicit: "Do NOT let short-term momentum create unrealistic
deterministic drift." Phase 3 measured what happens when you don't — V3's
hand-tuned trend adjustment supplied up to 65% of its entry evidence and its
trend-dependent trades scored 71.7% against 84.4% for buffer-sufficient ones.
Phase 4 then showed that even a properly fitted momentum family adds nothing.

``drift_shrinkage`` therefore defaults to 0.0: no drift at all. The parameter
exists so the assumption can be TESTED rather than assumed, and any non-zero
value must earn its place out of sample.

PERFORMANCE

Rows are grouped by ``seconds_remaining``. The scan grid has only 16 distinct
values, so every row in a group needs the same number of steps and the whole
group simulates as one array operation. Rows are then chunked so peak memory
stays bounded regardless of dataset size.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from .distributions import TerminalEstimate

DEFAULT_SEED = 20260813
DEFAULT_PATHS = 2000
DEFAULT_STEP_SECONDS = 15.0
MIN_SIGMA = 1e-9


@dataclass
class MonteCarloConfig:
    n_paths: int = DEFAULT_PATHS
    step_seconds: float = DEFAULT_STEP_SECONDS
    seed: int = DEFAULT_SEED
    #: 0.0 = no drift. See the module docstring before changing this.
    drift_shrinkage: float = 0.0
    #: "gaussian" or "empirical" (bootstrapped standardized innovations)
    innovation: str = "gaussian"
    row_chunk: int = 400
    max_steps: int = 120


class MonteCarloEngine:
    """Path simulation of the terminal outcome, vectorized over rows and paths."""

    def __init__(
        self,
        config: Optional[MonteCarloConfig] = None,
        innovation_pool: Optional[np.ndarray] = None,
    ) -> None:
        self.config = config or MonteCarloConfig()
        # Standardized 1-minute returns from TRAINING data only, used when
        # innovation == "empirical". Never sourced from evaluation rows.
        self._pool = None
        if innovation_pool is not None:
            pool = np.asarray(innovation_pool, dtype="float64")
            pool = pool[np.isfinite(pool)]
            if len(pool) >= 100:
                # Re-standardize so the pool has unit variance; scale is
                # supplied separately by sigma_remaining.
                std = pool.std(ddof=1)
                if std > MIN_SIGMA:
                    self._pool = (pool - pool.mean()) / std

    @property
    def has_empirical_pool(self) -> bool:
        return self._pool is not None

    # ------------------------------------------------------------------

    def _draw(self, rng: np.random.Generator, shape: tuple) -> np.ndarray:
        if self.config.innovation == "empirical" and self._pool is not None:
            idx = rng.integers(0, len(self._pool), size=shape)
            return self._pool[idx]
        return rng.standard_normal(shape)

    def run(
        self,
        *,
        spot: np.ndarray,
        reference: np.ndarray,
        seconds_remaining: np.ndarray,
        sigma_1m: np.ndarray,
        drift_1m: Optional[np.ndarray] = None,
        name: str = "monte_carlo",
    ) -> TerminalEstimate:
        """Simulate terminal outcomes. Deterministic for a fixed seed.

        ``sigma_1m`` is the per-minute realized volatility in return space —
        the same quantity Normal-Z scales by sqrt(minutes remaining).
        """
        spot = np.asarray(spot, dtype="float64")
        reference = np.asarray(reference, dtype="float64")
        seconds = np.asarray(seconds_remaining, dtype="float64")
        sigma_1m = np.maximum(np.asarray(sigma_1m, dtype="float64"), MIN_SIGMA)
        n = len(spot)

        drift = (
            np.zeros(n, dtype="float64") if drift_1m is None
            else np.asarray(drift_1m, dtype="float64") * self.config.drift_shrinkage
        )

        p_yes = np.zeros(n, dtype="float64")
        p_cross = np.zeros(n, dtype="float64")
        q05 = np.zeros(n, dtype="float64")
        q50 = np.zeros(n, dtype="float64")
        q95 = np.zeros(n, dtype="float64")
        expected_buffer = np.zeros(n, dtype="float64")

        cfg = self.config
        # Group by horizon: the scan grid has few distinct values, so each
        # group shares a step count and simulates as one array.
        for horizon in np.unique(seconds):
            group = np.flatnonzero(seconds == horizon)
            steps = int(np.clip(round(horizon / cfg.step_seconds), 1, cfg.max_steps))
            step_minutes = (horizon / steps) / 60.0

            for start in range(0, len(group), cfg.row_chunk):
                idx = group[start : start + cfg.row_chunk]
                # Seed per (horizon, chunk) so results are reproducible and
                # independent of how rows happen to be ordered.
                rng = np.random.default_rng(
                    (cfg.seed, int(horizon), int(start), len(idx))
                )
                self._simulate_chunk(
                    rng, idx, steps, step_minutes,
                    spot, reference, sigma_1m, drift,
                    p_yes, p_cross, q05, q50, q95, expected_buffer,
                )

        estimate = TerminalEstimate(
            name=name,
            p_yes=p_yes,
            quantile_05=q05,
            quantile_50=q50,
            quantile_95=q95,
            expected_terminal_buffer=expected_buffer,
            p_cross_reference=p_cross,
            # Binomial standard error of the simulated proportion. This is
            # SAMPLING error only -- it says nothing about whether the model
            # itself is right.
            standard_error=np.sqrt(
                np.clip(p_yes * (1.0 - p_yes), 0.0, None) / cfg.n_paths
            ),
            sample_size=np.full(n, cfg.n_paths, dtype="float64"),
            notes=(
                f"{cfg.n_paths} paths, {cfg.innovation} innovations, "
                f"drift_shrinkage={cfg.drift_shrinkage}"
            ),
        )
        estimate.diagnostics["config"] = {
            "n_paths": cfg.n_paths,
            "step_seconds": cfg.step_seconds,
            "seed": cfg.seed,
            "innovation": cfg.innovation,
            "drift_shrinkage": cfg.drift_shrinkage,
            "empirical_pool": bool(self._pool is not None),
        }
        return estimate.validate()

    def _simulate_chunk(
        self, rng, idx, steps, step_minutes,
        spot, reference, sigma_1m, drift,
        p_yes, p_cross, q05, q50, q95, expected_buffer,
    ) -> None:
        m = len(idx)
        paths = self.config.n_paths

        step_sigma = (sigma_1m[idx] * np.sqrt(step_minutes)).reshape(m, 1)
        step_drift = (drift[idx] * step_minutes).reshape(m, 1)
        s0 = spot[idx].reshape(m, 1)
        ref = reference[idx].reshape(m, 1)

        # Running cumulative return, and whether the reference was touched.
        cumulative = np.zeros((m, paths), dtype="float64")
        started_above = (s0 > ref)
        crossed = np.zeros((m, paths), dtype=bool)

        for _ in range(steps):
            cumulative += step_drift + step_sigma * self._draw(rng, (m, paths))
            price = s0 * (1.0 + cumulative)
            now_above = price > ref
            # A crossing is any step at which the side flips relative to start.
            crossed |= now_above != started_above

        terminal = s0 * (1.0 + cumulative)
        p_yes[idx] = (terminal > ref).mean(axis=1)
        p_cross[idx] = crossed.mean(axis=1)
        q05[idx] = np.percentile(terminal, 5, axis=1)
        q50[idx] = np.percentile(terminal, 50, axis=1)
        q95[idx] = np.percentile(terminal, 95, axis=1)
        expected_buffer[idx] = ((terminal.mean(axis=1) - ref.ravel()) / ref.ravel())
