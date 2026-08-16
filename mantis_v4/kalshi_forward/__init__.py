"""Isolated, append-only Kalshi forward-shadow research infrastructure."""

from .core import (
    ACTIONABILITY,
    ASSETS,
    MODEL_POLICY,
    REFERENCE_POLICY,
    SAMPLE_LABEL,
    SHADOW_POLICIES,
    ShadowStore,
    audit_store,
    build_observation,
    build_resolution,
    deterministic_contract_id,
    render_audit,
    shadow_report,
    simulate_shadow,
)

__all__ = [
    "ACTIONABILITY", "ASSETS", "MODEL_POLICY", "REFERENCE_POLICY",
    "SAMPLE_LABEL", "SHADOW_POLICIES", "ShadowStore", "audit_store",
    "build_observation", "build_resolution", "deterministic_contract_id",
    "render_audit", "shadow_report", "simulate_shadow",
]
