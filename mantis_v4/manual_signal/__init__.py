"""Experimental, anonymous, manual-only Kalshi signal research mode."""

from .core import (
    ACTIONABILITY,
    POLICY_VERSION,
    FeeMetadata,
    KalshiFeeModel,
    ManualSignalStore,
    initial_manual_selection,
    evaluate_candidate,
    select_primary_signal,
)

__all__ = [
    "ACTIONABILITY", "POLICY_VERSION", "FeeMetadata", "KalshiFeeModel",
    "ManualSignalStore", "initial_manual_selection", "evaluate_candidate", "select_primary_signal",
]
