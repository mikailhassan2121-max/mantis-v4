"""
MANTIS V4 — empirical conditional bootstrap (Phase 5 section 4).

Resamples ACTUAL historical remaining-window return fragments rather than
drawing from a fitted distribution. Nothing is assumed about shape, skew or
tails: whatever the market did in comparable states is what gets resampled.

CONDITIONING, AND THE POINT AT WHICH IT BECOMES SELF-DECEPTION

Section 4 lists many things a fragment could be matched on — horizon,
volatility bucket, buffer-z bucket, regime, asset, market direction — and then
adds the warning that matters more than the list:

    "Do not over-condition until sample size collapses."

That warning is the design constraint here. Conditioning on all six at once
would leave a handful of fragments per cell, and a probability read off eight
samples is noise with a decimal point. So conditioning is applied in a strict
PRIORITY ORDER and relaxed automatically the moment a cell falls below
``min_samples``:

    horizon + volatility + asset      most specific
    horizon + volatility
    horizon
    global                            least specific

The level actually used is recorded per row, so a reader can see how much
conditioning survived rather than having to trust that it did.

INSUFFICIENT SAMPLES ARE REPORTED, NOT PAPERED OVER

When even the global pool is too small, the row is flagged
``BOOTSTRAP INSUFFICIENT SAMPLE`` and its probability is returned as NaN.
Section 4: "Do not fabricate confidence."

TRAINING DATA ONLY

``fit`` receives training rows explicitly. There is no code path that reads
evaluation or holdout rows, and the fragment pools are frozen before any
prediction is made.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np

from .distributions import TerminalEstimate

MIN_SIGMA = 1e-9

# Conditioning levels, most specific first.
LEVEL_HORIZON_VOL_ASSET = 3
LEVEL_HORIZON_VOL = 2
LEVEL_HORIZON = 1
LEVEL_GLOBAL = 0
LEVEL_NONE = -1

LEVEL_NAMES = {
    LEVEL_HORIZON_VOL_ASSET: "horizon+vol+asset",
    LEVEL_HORIZON_VOL: "horizon+vol",
    LEVEL_HORIZON: "horizon",
    LEVEL_GLOBAL: "global",
    LEVEL_NONE: "INSUFFICIENT",
}


@dataclass
class BootstrapDiagnostics:
    level_counts: dict[str, int] = field(default_factory=dict)
    insufficient_rows: int = 0
    total_rows: int = 0
    pool_sizes: dict[str, int] = field(default_factory=dict)

    @property
    def insufficient_fraction(self) -> float:
        return 0.0 if self.total_rows == 0 else self.insufficient_rows / self.total_rows


class EmpiricalBootstrapModel:
    """Conditional resampling of historical standardized terminal returns."""

    name = "empirical_bootstrap"

    def __init__(
        self,
        *,
        horizon_edges: Sequence[float] = (60, 150, 300, 450, 600, 901),
        vol_quantiles: Sequence[float] = (1 / 3, 2 / 3),
        min_samples: int = 250,
        seed: int = 20260813,
    ) -> None:
        self.horizon_edges = tuple(horizon_edges)
        self.vol_quantiles = tuple(vol_quantiles)
        self.min_samples = min_samples
        self.seed = seed

        self._pools: dict[tuple, np.ndarray] = {}
        self._vol_edges: Optional[np.ndarray] = None
        self._fitted = False

    # ------------------------------------------------------------------

    def _horizon_bucket(self, seconds: np.ndarray) -> np.ndarray:
        return np.searchsorted(np.asarray(self.horizon_edges), seconds, side="left")

    def _vol_bucket(self, sigma_1m: np.ndarray) -> np.ndarray:
        if self._vol_edges is None or len(self._vol_edges) == 0:
            return np.zeros(len(sigma_1m), dtype="int64")
        return np.searchsorted(self._vol_edges, sigma_1m, side="left")

    def fit(
        self,
        *,
        standardized_returns: np.ndarray,
        seconds_remaining: np.ndarray,
        sigma_1m: np.ndarray,
        asset: np.ndarray,
    ) -> "EmpiricalBootstrapModel":
        r = np.asarray(standardized_returns, dtype="float64")
        seconds = np.asarray(seconds_remaining, dtype="float64")
        sigma = np.asarray(sigma_1m, dtype="float64")
        asset = np.asarray(asset)

        keep = np.isfinite(r) & np.isfinite(seconds) & np.isfinite(sigma)
        r, seconds, sigma, asset = r[keep], seconds[keep], sigma[keep], asset[keep]

        # Volatility bucket edges come from TRAINING data only.
        self._vol_edges = np.quantile(sigma, self.vol_quantiles) if len(sigma) else np.array([])

        horizon = self._horizon_bucket(seconds)
        vol = self._vol_bucket(sigma)

        self._pools = {}
        self._pools[("global",)] = np.sort(r)

        for h in np.unique(horizon):
            mask_h = horizon == h
            pool_h = r[mask_h]
            if len(pool_h) >= self.min_samples:
                self._pools[("h", int(h))] = np.sort(pool_h)

            for v in np.unique(vol[mask_h]):
                mask_hv = mask_h & (vol == v)
                pool_hv = r[mask_hv]
                if len(pool_hv) >= self.min_samples:
                    self._pools[("hv", int(h), int(v))] = np.sort(pool_hv)

                for a in np.unique(asset[mask_hv]):
                    mask_hva = mask_hv & (asset == a)
                    pool_hva = r[mask_hva]
                    if len(pool_hva) >= self.min_samples:
                        self._pools[("hva", int(h), int(v), str(a))] = np.sort(pool_hva)

        self._fitted = True
        return self

    # ------------------------------------------------------------------

    def _select_pool(self, h: int, v: int, a: str) -> tuple[Optional[np.ndarray], int]:
        """Most specific pool that clears min_samples, relaxing as needed."""
        for key, level in (
            (("hva", h, v, a), LEVEL_HORIZON_VOL_ASSET),
            (("hv", h, v), LEVEL_HORIZON_VOL),
            (("h", h), LEVEL_HORIZON),
            (("global",), LEVEL_GLOBAL),
        ):
            pool = self._pools.get(key)
            if pool is not None and len(pool) >= self.min_samples:
                return pool, level
        return None, LEVEL_NONE

    def predict(
        self,
        *,
        buffer_pct: np.ndarray,
        sigma_remaining: np.ndarray,
        seconds_remaining: np.ndarray,
        sigma_1m: np.ndarray,
        asset: np.ndarray,
    ) -> tuple[TerminalEstimate, BootstrapDiagnostics]:
        if not self._fitted:
            raise RuntimeError("EmpiricalBootstrapModel used before fit")

        buffer_pct = np.asarray(buffer_pct, dtype="float64")
        sigma_rem = np.maximum(np.asarray(sigma_remaining, dtype="float64"), MIN_SIGMA)
        seconds = np.asarray(seconds_remaining, dtype="float64")
        sigma = np.asarray(sigma_1m, dtype="float64")
        asset = np.asarray(asset)

        z = buffer_pct / sigma_rem
        n = len(z)

        horizon = self._horizon_bucket(seconds)
        vol = self._vol_bucket(sigma)

        p_yes = np.full(n, np.nan, dtype="float64")
        sample_size = np.zeros(n, dtype="float64")
        levels = np.full(n, LEVEL_NONE, dtype="int64")

        diagnostics = BootstrapDiagnostics(total_rows=n)

        # One pool lookup per (horizon, vol, asset) cell rather than per row.
        combos = {}
        for i in range(n):
            combos.setdefault((int(horizon[i]), int(vol[i]), str(asset[i])), []).append(i)

        for (h, v, a), rows in combos.items():
            pool, level = self._select_pool(h, v, a)
            rows = np.asarray(rows)
            levels[rows] = level
            if pool is None:
                diagnostics.insufficient_rows += len(rows)
                continue
            # P(YES) = P(standardized terminal return > -z), read off the
            # empirical CDF of the selected fragment pool.
            position = np.searchsorted(pool, -z[rows], side="left")
            raw = 1.0 - position / len(pool)
            floor = 1.0 / (2.0 * len(pool))
            p_yes[rows] = np.clip(raw, floor, 1.0 - floor)
            sample_size[rows] = len(pool)

        for level in np.unique(levels):
            diagnostics.level_counts[LEVEL_NAMES[int(level)]] = int((levels == level).sum())
        diagnostics.pool_sizes = {
            "|".join(str(part) for part in key): len(pool)
            for key, pool in sorted(self._pools.items(), key=lambda kv: str(kv[0]))
        }

        with np.errstate(invalid="ignore"):
            standard_error = np.sqrt(
                np.clip(p_yes * (1.0 - p_yes), 0.0, None) / np.maximum(sample_size, 1.0)
            )

        estimate = TerminalEstimate(
            name=self.name,
            p_yes=p_yes,
            sample_size=sample_size,
            standard_error=standard_error,
            notes="conditional resampling of historical standardized fragments",
        )
        estimate.diagnostics["levels"] = levels
        estimate.diagnostics["insufficient_fraction"] = diagnostics.insufficient_fraction

        # NaN is a legitimate output here (insufficient sample), so validate
        # only the rows that produced a number.
        finite = np.isfinite(p_yes)
        if finite.any():
            block = p_yes[finite]
            if np.any(block < 0.0) or np.any(block > 1.0):
                raise ValueError("bootstrap produced a probability outside [0, 1]")

        return estimate, diagnostics
