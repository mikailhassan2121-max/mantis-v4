"""
MANTIS V4 — Phase 3 backtesting and dataset construction.

Everything here is research infrastructure. It trains nothing, tunes nothing,
and claims nothing about profitability.

Reading order:
    view.py         the leakage barrier -- read this first
    features.py     leakage-safe features (V3-faithful and corrected V4)
    strategies.py   the four required baselines, one of them NOT EVALUABLE
    replay.py       the event-driven backtester
    metrics.py      classification metrics (deliberately NO economic fields)
    splits.py       purged/embargoed walk-forward, for Phase 5
    dataset.py      schema + writer, with provenance enforcement
    diagnostics.py  data quality + cross-asset correlation
    history.py      bar loading behind a replaceable interface
"""

from .dataset import (
    DATASET_COLUMNS,
    ProvenanceViolation,
    replay_to_row,
    write_dataset,
    write_schema_documentation,
)
from .diagnostics import (
    BarQualityReport,
    ContractQualityReport,
    CrossAssetReport,
    analyse_bars,
    analyse_contracts,
    analyse_cross_asset,
)
from .features import compute_features, compute_rsi, compute_v3_rsi, normal_cdf
from .history import (
    HistoricalBarProvider,
    HistoryLoadReport,
    InMemoryHistoryProvider,
    YFinanceHistoryProvider,
    load_universe,
)
from .metrics import (
    StrategyMetrics,
    StrategyReport,
    brier_score,
    build_report,
    evaluate,
    log_loss,
    wilson_interval,
)
from .replay import ContractReplay, EventDrivenBacktester, ScanRecord
from .splits import LabelledSpan, Split, purged_walk_forward, spans_from_replays
from .strategies import (
    Action,
    BacktestStrategy,
    Decision,
    GeminiEdgeStrategy,
    NormalZBaselineStrategy,
    SpotVsReferenceStrategy,
    StrategyNotEvaluable,
    V3ResolutionStrategy,
    default_strategies,
)
from .view import HistoricalMarketView, ProxyReference, derive_proxy_reference

__all__ = [
    "Action", "BacktestStrategy", "BarQualityReport", "ContractQualityReport",
    "ContractReplay", "CrossAssetReport", "DATASET_COLUMNS", "Decision",
    "EventDrivenBacktester", "GeminiEdgeStrategy", "HistoricalBarProvider",
    "HistoricalMarketView", "HistoryLoadReport", "InMemoryHistoryProvider",
    "LabelledSpan", "NormalZBaselineStrategy", "ProvenanceViolation",
    "ProxyReference", "ScanRecord", "SpotVsReferenceStrategy", "Split",
    "StrategyMetrics", "StrategyNotEvaluable", "StrategyReport",
    "V3ResolutionStrategy", "YFinanceHistoryProvider", "analyse_bars",
    "analyse_contracts", "analyse_cross_asset", "brier_score", "build_report",
    "compute_features", "compute_rsi", "compute_v3_rsi", "default_strategies",
    "derive_proxy_reference", "evaluate", "load_universe", "log_loss",
    "normal_cdf", "purged_walk_forward", "replay_to_row", "spans_from_replays",
    "wilson_interval", "write_dataset", "write_schema_documentation",
]
