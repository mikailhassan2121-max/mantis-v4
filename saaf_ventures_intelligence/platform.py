"""Versioned capability and safety manifest for the completed manual research platform."""
from __future__ import annotations


def platform_manifest() -> dict:
    return {"platform_version":"SVI_MANUAL_RESEARCH_PLATFORM_V1","package_version":"1.0.0",
        "components":{"agent_registry":"SVI_AGENT_REGISTRY_V2",
            "data_normalization":"SVI_DATA_NORMALIZATION_V1",
            "audit_schema":6,"historical_replay":"SVI_HISTORICAL_REPLAY_V1",
            "temporal_drift":"SVI_TEMPORAL_DRIFT_V1",
            "specialist_lifecycle":"SVI_SPECIALIST_LIFECYCLE_V1",
            "admission_governance":"SVI_SPECIALIST_ADMISSION_V4"},
        "capabilities":{"typed_contracts":True,"normalized_observations":True,
            "append_only_evidence":True,"verified_resolution_scoring":True,
            "chronological_replay":True,"calibration_reporting":True,
            "cross_asset_attribution":True,"drift_reporting":True,
            "read_only_command_center":True},
        "safety":{"execution_mode":"MANUAL_ONLY","observation_only":True,
            "brokerage_authentication":False,"order_execution":False,
            "portfolio_sizing":False,"pnl_backtest":False,"automatic_promotion":False,
            "automatic_policy_tuning":False},"complete":True}
