"""Experimental, anonymous, manual-only Kalshi signal research mode."""

from .core import (
    ACTIONABILITY,
    POLICY_VERSION,
    V21_POLICY_VERSION,
    V21_POLICY,
    V22_POLICY_VERSION,
    V22_POLICY,
    FeeMetadata,
    KalshiFeeModel,
    ManualSignalStore,
    initial_manual_selection,
    evaluate_candidate,
    select_primary_signal,
)

__all__ = [
    "ACTIONABILITY", "POLICY_VERSION", "V21_POLICY_VERSION", "V21_POLICY", "V22_POLICY_VERSION", "V22_POLICY", "FeeMetadata", "KalshiFeeModel",
    "ManualSignalStore", "initial_manual_selection", "evaluate_candidate", "select_primary_signal",
]
