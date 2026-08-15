"""
MANTIS V4 — Market Analysis and Neuro-Tactical Intraday Signal System.

Research/decision support for fixed-clock 15-minute crypto event contracts.

Signals only. MANTIS does not connect to a broker to place orders and never
will: master prompt section 13 requires it remain advisory. Underlying crypto
movement is NOT the same thing as event-contract P&L.

V3 (``mantis_15m_resolution_v3.py``) is a protected baseline and is never
modified. This package is the V4 rebuild; the runnable entry point is
``mantis_15m_resolution_v4.py`` at the repository root.

PHASE STATUS
    Phase 1  COMPLETE  audit -> research/PHASE1_V3_AUDIT.md
    Phase 2  COMPLETE  contract semantics, providers, quality gates,
                       resolution/outcome recording, configuration
    Phase 3+ PENDING   dataset, backtester, models, calibration, ensemble, UI

Phase 2 contains NO probability model. Every decision it produces is NO TRADE.
"""

__version__ = "4.10.0"

from .clock import Clock, FrozenClock, Instant, LoopPacer
from .config import MantisConfig, WebullCredentials
from .contracts import (
    ContractQuote,
    ContractSpec,
    ContractStatus,
    ContractWindow,
    ReferenceSource,
    SettlementRule,
)
from .engine import AssetScan, MantisEngine, ScanResult
from .quality import DataQualityReport, QualityThresholds, evaluate_data_quality
from .recording import ContractRecorder, PredictionRecord
from .resolution import ResolutionEngine, find_terminal_price

__all__ = [
    "AssetScan",
    "Clock",
    "ContractQuote",
    "ContractRecorder",
    "ContractSpec",
    "ContractStatus",
    "ContractWindow",
    "DataQualityReport",
    "FrozenClock",
    "Instant",
    "LoopPacer",
    "MantisConfig",
    "MantisEngine",
    "PredictionRecord",
    "QualityThresholds",
    "ReferenceSource",
    "ResolutionEngine",
    "ScanResult",
    "SettlementRule",
    "WebullCredentials",
    "__version__",
    "evaluate_data_quality",
    "find_terminal_price",
]
