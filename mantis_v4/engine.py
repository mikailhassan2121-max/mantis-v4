"""
MANTIS V4 — scan engine (Phase 2 skeleton).

This wires the Phase 2 subsystems into one scan cycle:

    capture instant (ONCE)
      -> resolve contract window (ONCE)
      -> detect rollover, resolve any matured contracts
      -> for each asset: bars -> contract spec -> data-quality gate
      -> record the contract observation and the prediction row
      -> return an immutable scan result for the UI

WHAT THIS PHASE DELIBERATELY DOES NOT DO

There is no probability model here. Phase 2's mandate is contract semantics,
providers, quality gates, recording and configuration -- not modelling. Every
decision this engine produces is therefore ``NO_TRADE`` with the reason
"PHASE2_NO_MODEL".

That is not a placeholder failure; it is the correct behaviour for a system
that cannot yet estimate a probability. Master prompt section 0 makes NO TRADE
a first-class decision, and a MANTIS that abstains because it genuinely has no
model is behaving exactly as specified. Phases 4-7 supply the probability,
ensemble, fragility and entry logic that turn some of these into ENTER.

The recording, however, is fully live from this phase onward -- which is the
point. Every contract observed from now on is captured with its reference, its
spot path, and its eventual outcome, so that MANTIS starts building its own
clean dataset immediately rather than after the model exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from zoneinfo import ZoneInfo

from .clock import Clock, Instant, LoopPacer, assert_window_alignable, load_timezone
from .config import MantisConfig
from .contracts import ContractSpec, ContractWindow
from .providers.base import BarSet, MarketDataProvider
from .providers.chain import ContractProviderChain
from .quality import DataQualityReport, QualityThresholds, evaluate_data_quality
from .recording import ContractRecorder, PredictionRecord
from .resolution import ResolutionEngine

PHASE2_DECISION = "NO_TRADE"
PHASE2_REASON = "PHASE2_NO_MODEL - probability engine arrives in Phase 4-7"


@dataclass
class AssetScan:
    """Everything one asset produced in one scan."""

    asset: str
    bars: Optional[BarSet] = None
    spec: Optional[ContractSpec] = None
    quality: Optional[DataQualityReport] = None
    spot: Optional[float] = None
    buffer_abs: Optional[float] = None
    buffer_pct: Optional[float] = None
    decision: str = PHASE2_DECISION
    decision_reason: str = PHASE2_REASON
    economics_available: bool = False

    @property
    def tradeable(self) -> bool:
        """Whether the data gate would permit an entry at all."""
        return self.quality is not None and self.quality.passed

    @property
    def status_text(self) -> str:
        if self.quality is None:
            return "NO DATA"
        return self.quality.summary


@dataclass
class ScanResult:
    """One complete scan. Immutable snapshot handed to the UI."""

    instant: Instant
    window: ContractWindow
    seconds_remaining: float
    rolled_over: bool
    assets: dict[str, AssetScan] = field(default_factory=dict)
    resolved_contracts: list[dict] = field(default_factory=list)

    @property
    def any_tradeable(self) -> bool:
        return any(scan.tradeable for scan in self.assets.values())


class MantisEngine:
    """Phase 2 scan engine."""

    def __init__(
        self,
        config: MantisConfig,
        *,
        clock: Clock,
        market_data: MarketDataProvider,
        contracts: ContractProviderChain,
        recorder: ContractRecorder,
    ) -> None:
        self.config = config
        self.clock = clock
        self.market_data = market_data
        self.contracts = contracts
        self.recorder = recorder

        self.timezone: ZoneInfo = load_timezone(config.contract_timezone)
        assert_window_alignable(self.timezone, config.contract_window_minutes)

        self.thresholds = QualityThresholds(
            max_data_age_seconds=config.max_data_age_seconds,
            max_quote_age_seconds=config.max_quote_age_seconds,
            min_candles=config.min_candles,
            stale_price_min_distinct_closes=config.stale_price_min_distinct_closes,
            stale_price_lookback_bars=config.stale_price_lookback_bars,
            min_realized_vol_1m=config.min_realized_vol_1m,
        )

        self._bars_this_scan: dict[str, Optional[BarSet]] = {}
        self.resolution = ResolutionEngine(
            recorder,
            self._lookup_bars,
            grace_seconds=config.resolution_grace_seconds,
            max_attempts=config.resolution_max_attempts,
        )
        self.pacer = LoopPacer(clock, config.scan_interval_seconds)

        self._last_window: Optional[ContractWindow] = None
        self._scan_counter = 0

    # ------------------------------------------------------------------

    def _lookup_bars(self, asset: str) -> Optional[BarSet]:
        """Bars for this scan. Cached so one scan makes one fetch per asset."""
        if asset in self._bars_this_scan:
            return self._bars_this_scan[asset]
        instant = getattr(self, "_current_instant", None) or self.clock.capture()
        bars = self.market_data.get_bars(asset, instant)
        self._bars_this_scan[asset] = bars
        return bars

    def startup_recovery(self) -> dict:
        """Resolve anything a previous run left open. Call once, at boot."""
        instant = self.clock.capture()
        self._current_instant = instant
        self._bars_this_scan = {}
        return self.resolution.recover_on_startup(instant)

    # ------------------------------------------------------------------

    def scan(self) -> ScanResult:
        """Run one full scan cycle. Never raises for provider reasons."""
        # THE clock read for this entire scan (audit A-4). Nothing downstream
        # reads the clock again, so a boundary cannot fall mid-scan.
        instant = self.clock.capture()
        self._current_instant = instant
        self._bars_this_scan = {}
        self._scan_counter += 1

        window = ContractWindow.for_instant(
            instant, self.timezone, self.config.contract_window_minutes
        )

        rolled_over = self._last_window is not None and window != self._last_window
        self._last_window = window

        # Resolve matured contracts on every scan, and especially at rollover.
        resolved = self.resolution.resolve_due(instant)

        result = ScanResult(
            instant=instant,
            window=window,
            seconds_remaining=window.seconds_remaining(instant),
            rolled_over=rolled_over,
            resolved_contracts=resolved,
        )

        should_record = (self._scan_counter % self.config.record_every_n_scans) == 0

        for asset in self.config.active_assets:
            result.assets[asset] = self._scan_asset(asset, window, instant, should_record)

        return result

    def _scan_asset(
        self,
        asset: str,
        window: ContractWindow,
        instant: Instant,
        should_record: bool,
    ) -> AssetScan:
        scan = AssetScan(asset=asset)

        bars = self._lookup_bars(asset)
        scan.bars = bars
        scan.spot = None if bars is None else bars.last_close

        spec = self.contracts.get_contract_spec(asset, window, instant)
        scan.spec = spec

        economics = False
        if spec is not None:
            economics = spec.economics_available(instant, self.config.max_quote_age_seconds)
        scan.economics_available = economics

        scan.quality = evaluate_data_quality(
            asset=asset,
            bars=bars,
            window=window,
            instant=instant,
            spec=spec,
            thresholds=self.thresholds,
            require_economics=economics,
        )

        if spec is not None and spec.reference_price and scan.spot:
            scan.buffer_abs = scan.spot - float(spec.reference_price)
            scan.buffer_pct = scan.buffer_abs / float(spec.reference_price)

        # Phase 2 always abstains: there is no probability model yet.
        scan.decision = PHASE2_DECISION
        scan.decision_reason = (
            PHASE2_REASON if scan.tradeable
            else f"DATA HOLD - {scan.quality.summary}" if scan.quality else "NO DATA"
        )

        if spec is not None and should_record:
            self._record(scan, spec, window, instant, economics)

        return scan

    def _record(
        self,
        scan: AssetScan,
        spec: ContractSpec,
        window: ContractWindow,
        instant: Instant,
        economics: bool,
    ) -> None:
        """Persist the observation and the prediction. Never raises."""
        try:
            key = self.recorder.observe_contract(
                spec, instant, spot=scan.spot, economics_available=economics
            )
            quote = spec.quote
            self.recorder.record_prediction(
                PredictionRecord(
                    key=key,
                    contract_id=window.contract_id,
                    asset=scan.asset,
                    scan_utc=instant.utc,
                    seconds_remaining=window.seconds_remaining(instant),
                    elapsed_fraction=window.elapsed_fraction(instant),
                    spot=scan.spot,
                    reference_price=spec.reference_price,
                    reference_source=spec.reference_source.value,
                    buffer_abs=scan.buffer_abs,
                    buffer_pct=scan.buffer_pct,
                    yes_bid=quote.yes_bid,
                    yes_ask=quote.yes_ask,
                    no_bid=quote.no_bid,
                    no_ask=quote.no_ask,
                    quote_age_seconds=quote.age_seconds(instant),
                    spread=quote.yes_spread,
                    decision=scan.decision,
                    decision_reason=scan.decision_reason,
                    data_quality_passed=scan.tradeable,
                    data_quality_detail=scan.status_text,
                    features={
                        "bars": len(scan.bars) if scan.bars else 0,
                        "bar_source": scan.bars.source if scan.bars else None,
                        "last_bar_partial": scan.bars.last_bar_is_partial if scan.bars else None,
                        "from_cache": scan.bars.from_cache if scan.bars else None,
                        "contract_provider": spec.provider_name,
                        "quality_failures": [
                            c.name for c in (scan.quality.failures if scan.quality else [])
                        ],
                    },
                    model_version=self.recorder.model_version,
                )
            )
        except Exception as exc:  # noqa: BLE001 - recording must not stop the loop
            scan.decision_reason = f"{scan.decision_reason} | RECORDING ERROR: {type(exc).__name__}"
