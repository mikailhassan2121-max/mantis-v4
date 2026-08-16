"""Transparent Step 8 Kalshi reference-risk research policy.

No live selector, UI, voice, economics, forward store, or execution dependency is
permitted here. A ROBUST result means robust to an explicitly supplied symmetric
proxy perturbation only; it is not proof of the unknown current CF RTI value.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import math

import numpy as np
import pandas as pd

POLICY_VERSION = "KALSHI_REFERENCE_RISK_V1_SHADOW"
STATUS = "RESEARCH_SHADOW_ONLY"
EVENT_REFERENCE_PROVENANCE = "KALSHI_CRYPTO15M_CF_BENCHMARKS"
CURRENT_VALUE_PROVENANCE = "YAHOO_PROXY"
OPENED_HOLDOUT_LABEL = "PREVIOUSLY_OPENED_HOLDOUT_NOT_NEW_SEALED"
FIXED_BOUNDS_BPS = (Decimal("1"), Decimal("2"), Decimal("5"), Decimal("10"))
EMPIRICAL_QUANTILES = ("P50", "P95", "P99")


@dataclass(frozen=True)
class ReferenceRiskAssessment:
    asset: str
    target: Decimal
    proxy_current: Decimal
    distance_bps: Decimal | None
    uncertainty_bound_bps: Decimal | None
    model_side: str | None
    perturbed_low_side: str | None
    perturbed_high_side: str | None
    classification: str
    event_reference_provenance: str = EVENT_REFERENCE_PROVENANCE
    current_value_provenance: str = CURRENT_VALUE_PROVENANCE
    policy_version: str = POLICY_VERSION
    actionability: str = "SHADOW_ONLY"


def reference_distance_bps(proxy_current: Decimal, target: Decimal) -> Decimal:
    if not proxy_current.is_finite() or not target.is_finite() or proxy_current <= 0 or target <= 0:
        raise ValueError("positive finite proxy and Kalshi target required")
    return Decimal("10000") * (proxy_current - target) / target


def assess_reference_risk(*, asset: str, target: Decimal | None,
                          proxy_current: Decimal | None,
                          uncertainty_bound_bps: Decimal | None) -> ReferenceRiskAssessment:
    if target is None or proxy_current is None or uncertainty_bound_bps is None:
        return ReferenceRiskAssessment(asset, target or Decimal("NaN"), proxy_current or Decimal("NaN"),
                                       None, uncertainty_bound_bps, None, None, None, "REFERENCE_UNKNOWN")
    if not uncertainty_bound_bps.is_finite() or uncertainty_bound_bps < 0:
        return ReferenceRiskAssessment(asset, target, proxy_current, None, uncertainty_bound_bps,
                                       None, None, None, "REFERENCE_UNKNOWN")
    try:
        distance = reference_distance_bps(proxy_current, target)
    except ValueError:
        return ReferenceRiskAssessment(asset, target, proxy_current, None, uncertainty_bound_bps,
                                       None, None, None, "REFERENCE_UNKNOWN")
    low, high = distance - uncertainty_bound_bps, distance + uncertainty_bound_bps
    side = "YES" if distance >= 0 else "NO"
    low_side, high_side = ("YES" if low >= 0 else "NO"), ("YES" if high >= 0 else "NO")
    classification = "REFERENCE_ROBUST" if low_side == high_side == side else "REFERENCE_AMBIGUOUS"
    return ReferenceRiskAssessment(asset, target, proxy_current, distance, uncertainty_bound_bps,
                                   side, low_side, high_side, classification)


def fit_empirical_bounds(development_contracts: pd.DataFrame) -> dict[str, dict[str, Decimal]]:
    """Fit absolute proxy-target bounds from development contracts only."""
    required = {"asset", "yahoo_start_proxy", "kalshi_target"}
    if development_contracts.empty or required - set(development_contracts):
        raise ValueError("development contracts with actual Kalshi targets required")
    result = {}
    for asset, block in development_contracts.groupby("asset", sort=True):
        proxy = block.yahoo_start_proxy.to_numpy(float); target = block.kalshi_target.to_numpy(float)
        if len(block) < 20 or not np.isfinite(np.c_[proxy, target]).all() or np.any(target <= 0):
            raise ValueError(f"insufficient empirical reference data for {asset}")
        absolute = np.abs(10000 * (proxy-target)/target)
        result[str(asset)] = {name: Decimal(str(float(np.quantile(absolute, q))))
                              for name, q in (("P50", .50), ("P95", .95), ("P99", .99))}
    return result


def near_target_bucket(abs_distance_bps: float) -> str:
    if not math.isfinite(abs_distance_bps) or abs_distance_bps < 0:
        return "UNKNOWN"
    if abs_distance_bps < 1: return "LT_1BP"
    if abs_distance_bps < 2: return "1_2BP"
    if abs_distance_bps < 5: return "2_5BP"
    if abs_distance_bps < 10: return "5_10BP"
    return "GE_10BP"


def time_remaining_bucket(seconds: float) -> str:
    if not math.isfinite(seconds) or seconds < 0: return "UNKNOWN"
    if seconds <= 60: return "T_0_60"
    if seconds <= 120: return "T_60_120"
    if seconds <= 180: return "T_120_180"
    if seconds <= 240: return "T_180_240"
    if seconds <= 300: return "T_240_300"
    return "PRE_ENTRY_GT_300"


def evaluate_shadow_cohort(frame: pd.DataFrame, bounds_by_asset: dict[str, Decimal],
                           *, eligible_contracts: int) -> dict:
    required = {"asset", "spot", "kalshi_target", "outcome_yes", "p_yes"}
    if frame.empty or required - set(frame):
        raise ValueError("aligned shadow cohort required")
    rows = frame.copy()
    assessments = [assess_reference_risk(asset=str(a), target=Decimal(str(t)),
                                          proxy_current=Decimal(str(v)),
                                          uncertainty_bound_bps=bounds_by_asset.get(str(a)))
                   for a, v, t in zip(rows.asset, rows.spot, rows.kalshi_target)]
    rows["reference_risk"] = [x.classification for x in assessments]
    rows["distance_bps"] = [float(x.distance_bps) if x.distance_bps is not None else np.nan for x in assessments]
    robust = rows.reference_risk.eq("REFERENCE_ROBUST")
    pred_yes = rows.p_yes.to_numpy(float) >= .5; outcome = rows.outcome_yes.to_numpy(int).astype(bool)
    correct = pred_yes == outcome
    selected = rows.loc[robust].copy(); selected["correct"] = correct[robust]
    selected["pred_side"] = np.where(pred_yes[robust], "YES", "NO")
    result = {"eligible_contracts": int(eligible_contracts), "original_qualified": int(len(rows)),
              "shadow_qualified": int(robust.sum()),
              "coverage_of_eligible": float(robust.sum()/eligible_contracts),
              "coverage_retained_from_original": float(robust.mean()),
              "REFERENCE_ROBUST": int(robust.sum()),
              "REFERENCE_AMBIGUOUS": int(rows.reference_risk.eq("REFERENCE_AMBIGUOUS").sum()),
              "REFERENCE_UNKNOWN": int(rows.reference_risk.eq("REFERENCE_UNKNOWN").sum()),
              "sensitivity_side_flip_or_touch_rate": float(1-robust.mean())}
    if selected.empty:
        result.update({"accuracy": None, "yes": {"n": 0, "accuracy": None},
                       "no": {"n": 0, "accuracy": None}, "per_asset": {}})
        return result
    result["accuracy"] = float(selected.correct.mean())
    result["yes"] = _side_metrics(selected, "YES")
    result["no"] = _side_metrics(selected, "NO")
    result["per_asset"] = {asset: {"n": int(len(block)), "accuracy": float(block.correct.mean())}
                           for asset, block in selected.groupby("asset", sort=True)}
    return result


def _side_metrics(frame, side):
    block = frame[frame.pred_side == side]
    return {"n": int(len(block)), "accuracy": float(block.correct.mean()) if len(block) else None}


def format_shadow_diagnostic(assessment: ReferenceRiskAssessment) -> str:
    distance = "UNAVAILABLE" if assessment.distance_bps is None else f"{assessment.distance_bps:+.2f} bp"
    return "\n".join(["KALSHI REFERENCE SHADOW", "", assessment.asset.replace("-USD", ""),
        f"TARGET ............... {assessment.target}", f"PROXY CURRENT ........ {assessment.proxy_current}",
        f"DISTANCE ............. {distance}", f"REFERENCE RISK ....... {assessment.classification}",
        f"MODEL SIDE ........... {assessment.model_side or 'UNAVAILABLE'}",
        "ACTIONABILITY ........ SHADOW ONLY"])
