"""Leakage-resistant Phase 6 policy replay and contract-level evaluation."""
from __future__ import annotations

from dataclasses import asdict
from typing import Iterable, Sequence
import numpy as np
import pandas as pd

from .decision import Decision, DecisionInputs, DecisionPolicy, ProbabilityState, RiskDiagnostics, decide
from ..models.evaluation import clustered_accuracy

ENTRY_BUCKETS = (
    ("T-600 to T-450", 450, 600), ("T-450 to T-300", 300, 450),
    ("T-300 to T-180", 180, 300), ("T-180 to T-120", 120, 180),
    ("T-120 to T-60", 60, 120), ("T-60 to T-30", 30, 60),
    ("T-30 to resolution", 0, 30),
)


def entry_time_bucket(seconds: float) -> str:
    if seconds > 600:
        return "earlier than T-600"
    for label, low, high in ENTRY_BUCKETS:
        if low < seconds <= high or (low == 0 and 0 <= seconds <= high):
            return label
    return "outside"


def _inputs(row) -> DecisionInputs:
    return DecisionInputs(
        asset=str(row.asset), contract_id=str(row.contract_id),
        seconds_remaining=float(row.seconds_remaining),
        probability=ProbabilityState(float(row.p_yes), float(row.lower_bound)),
        risk=RiskDiagnostics(float(row.normal_z), float(row.fragility),
                             float(row.disagreement), float(row.crossing_risk),
                             str(getattr(row, "volatility_regime", "UNKNOWN")),
                             int(getattr(row, "crossings", 0))),
        data_fresh=bool(getattr(row, "data_fresh", True)),
        reference_valid=bool(getattr(row, "reference_valid", True)),
        contract_valid=bool(getattr(row, "contract_valid", True)),
        sufficient_history=bool(getattr(row, "sufficient_history", True)),
    )


def apply_policy_once(states: pd.DataFrame, policy: DecisionPolicy) -> pd.DataFrame:
    """Chronological replay. A contract enters at most once and holds to resolution."""
    required = {"contract_id", "asset", "group_key", "seconds_remaining", "p_yes",
                "lower_bound", "normal_z", "fragility", "disagreement", "crossing_risk",
                "outcome_yes"}
    missing = required - set(states)
    if missing:
        raise ValueError(f"missing Phase 6 columns: {sorted(missing)}")
    ordered = states.sort_values(["group_key", "asset", "seconds_remaining"],
                                 ascending=[True, True, False], kind="stable")
    records = []
    for (_, _), block in ordered.groupby(["contract_id", "asset"], sort=False):
        entered = None
        last = None
        for row in block.itertuples(index=False):
            result = decide(_inputs(row), policy)
            last = (row, result)
            if result.decision in (Decision.ENTER_YES, Decision.ENTER_NO):
                entered = (row, result)
                break
            if result.decision in (Decision.DATA_HOLD, Decision.NO_TRADE):
                continue
        row, result = entered or last
        records.append({
            "policy": policy.name, "contract_id": row.contract_id, "asset": row.asset,
            "group_key": row.group_key, "seconds_remaining": float(row.seconds_remaining),
            "entry_bucket": entry_time_bucket(float(row.seconds_remaining)),
            "decision": result.decision.value, "reason_code": result.reason_code,
            "side": result.side, "p_yes": float(row.p_yes),
            "confidence": max(float(row.p_yes), 1-float(row.p_yes)),
            "lower_bound": float(row.lower_bound), "fragility": float(row.fragility),
            "disagreement": float(row.disagreement), "normal_z": float(row.normal_z),
            "crossing_risk": float(row.crossing_risk), "outcome_yes": int(row.outcome_yes),
            "entered": entered is not None, "ev_status": result.ev_status,
        })
    return pd.DataFrame(records)


def evaluate_entries(entries: pd.DataFrame, contracts_observed: int | None = None,
                     n_boot: int = 500, seed: int = 20260814) -> dict:
    total = int(contracts_observed if contracts_observed is not None else len(entries))
    traded = entries[entries.entered.astype(bool)].copy()
    n = len(traded)
    base = {"contracts_observed": total, "contracts_traded": n,
            "coverage": n / total if total else 0.0,
            "abstention_rate": 1 - n / total if total else 1.0}
    if not n:
        return {**base, "accuracy": None, "error_rate": None, "ci_low": None,
                "ci_high": None, "yes": {}, "no": {}, "per_asset": {},
                "entry_time_distribution": {}}
    correct = np.where(traded.side.eq("YES"), traded.outcome_yes.eq(1),
                       traded.outcome_yes.eq(0)).astype(float)
    ci = clustered_accuracy(correct, traded.group_key.to_numpy(), n_boot=n_boot, seed=seed)
    traded["correct"] = correct
    def slice_stats(x):
        return {"n": int(len(x)), "accuracy": float(x.correct.mean()) if len(x) else None}
    return {**base, "accuracy": ci.point, "error_rate": 1-ci.point,
            "ci_low": ci.low, "ci_high": ci.high, "n_windows": ci.n_groups,
            "yes": slice_stats(traded[traded.side.eq("YES")]),
            "no": slice_stats(traded[traded.side.eq("NO")]),
            "per_asset": {str(k): slice_stats(v) for k,v in traded.groupby("asset")},
            "entry_time_distribution": traded.entry_bucket.value_counts().to_dict(),
            "probability_distribution": traded.confidence.quantile([.05,.25,.5,.75,.95]).to_dict(),
            "fragility_distribution": traded.fragility.quantile([.05,.25,.5,.75,.95]).to_dict(),
            "disagreement_distribution": traded.disagreement.quantile([.05,.25,.5,.75,.95]).to_dict(),
            "average_conservative_bound": float(traded.lower_bound.mean())}


def select_policy_on_development(states: pd.DataFrame, candidates: Sequence[DecisionPolicy],
                                 validation_fraction: float = .25) -> tuple[DecisionPolicy, list[dict]]:
    """Select on final development windows only; callers must not pass holdout rows."""
    windows = states[["group_key", "window_epoch"]].drop_duplicates().sort_values("window_epoch")
    cut = max(1, int(round(len(windows) * (1-validation_fraction))))
    validation_groups = set(windows.group_key.iloc[cut:])
    validation = states[states.group_key.isin(validation_groups)]
    rows = []
    for order, policy in enumerate(candidates):
        entries = apply_policy_once(validation, policy)
        metrics = evaluate_entries(entries, contracts_observed=len(entries), n_boot=10)
        rows.append({"order": order, "policy": policy, **metrics})
    eligible = [r for r in rows if r["contracts_traded"] >= 30 and r["accuracy"] is not None]
    if not eligible:
        return candidates[0], [{k:v for k,v in r.items() if k != "policy"} for r in rows]
    # Precision first, then coverage, then declaration order: deterministic.
    best = max(eligible, key=lambda r: (r["accuracy"], r["coverage"], -r["order"]))
    return best["policy"], [{**{k:v for k,v in r.items() if k != "policy"},
                              "policy_name": r["policy"].name} for r in rows]
