"""
MANTIS V4 — event-driven backtester (Phase 3 requirements 4 and 5).

For every historical 15-minute contract this module:

  1. establishes start/end from the fixed :00/:15/:30/:45 boundaries
     (reusing the SAME ``ContractWindow`` the live system uses — not a
     re-implementation that could drift from it);
  2. derives the proxy reference from information available at contract start;
  3. walks forward chronologically on a fixed scan grid;
  4. at each scan builds a ``HistoricalMarketView`` that physically excludes
     future bars, and computes features from it alone;
  5. asks the strategy for WAIT / ENTER YES / ENTER NO / NO TRADE;
  6. on entry, FREEZES the decision timestamp and a deep-copied feature
     snapshot, and stops scanning that contract (hold to resolution);
  7. settles only AFTER the contract end, using the same terminal-price rule as
     the live resolution engine.

Entry logic never sees the terminal price. The terminal price is not fetched
until ``_settle`` runs, which happens after the scan loop has finished and
after the entry snapshot has been frozen.

THE FROZEN SNAPSHOT

``ContractReplay.entry_features`` is a deep copy taken at the instant of entry
and never written again. If a later scan or a settlement step could mutate it,
the "features at decision time" recorded in the dataset would silently become
"features at some later time", which is look-ahead wearing a disguise. Phase 3
requirement 15 asks for a test of exactly this; ``test_backtest_leakage``
provides it.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterator, Optional
from zoneinfo import ZoneInfo

import pandas as pd

from ..clock import UTC
from ..contracts import ContractWindow, SettlementRule
from ..resolution import DEFAULT_TERMINAL_TOLERANCE_SECONDS, find_terminal_price
from .features import compute_features, precompute_indicators
from .strategies import Action, BacktestStrategy, Decision, StrategyNotEvaluable
from .view import HistoricalMarketView, ProxyReference, derive_proxy_reference

# Section 26I asks for candidate entries on a fine grid (T-300, T-285, ...).
DEFAULT_SCAN_INTERVAL_SECONDS = 15


@dataclass(frozen=True)
class ScanRecord:
    """One evaluated scan. Immutable by construction."""

    scan_utc: datetime
    seconds_remaining: float
    elapsed_fraction: float
    spot: Optional[float]
    action: str
    p_yes: Optional[float]
    reason: str
    features_ok: bool


@dataclass
class ContractReplay:
    """The full history of one contract under one strategy."""

    asset: str
    window: ContractWindow
    strategy: str

    reference: Optional[float] = None
    reference_bar_utc: Optional[datetime] = None
    reference_verified: bool = False           # Phase 3 requirement 1: ALWAYS False
    reference_source: str = "PROXY_WINDOW_OPEN"
    economics_available: bool = False          # Phase 3 requirement 1: ALWAYS False

    scans: list[ScanRecord] = field(default_factory=list)
    scan_count: int = 0

    entered: bool = False
    entry_side: Optional[str] = None
    entry_timestamp: Optional[datetime] = None
    entry_spot: Optional[float] = None
    entry_p_yes: Optional[float] = None
    entry_reason: str = ""
    entry_features: Optional[dict[str, Any]] = None   # FROZEN at entry

    decision: str = "NO_TRADE"

    terminal_price: Optional[float] = None
    terminal_source: Optional[str] = None
    terminal_bar_utc: Optional[datetime] = None
    outcome_yes: Optional[bool] = None
    outcome_label: Optional[str] = None
    prediction_correct: Optional[int] = None

    data_quality_status: str = "OK"
    usable: bool = False
    skip_reason: str = ""
    regime: Optional[str] = None               # placeholder; Phase 7 fills it

    @property
    def contract_id(self) -> str:
        return self.window.contract_id

    @property
    def key(self) -> str:
        return self.window.key_for(self.asset)


class EventDrivenBacktester:
    """Replays historical contracts scan by scan, strictly forward in time."""

    def __init__(
        self,
        frames: dict[str, pd.DataFrame],
        timezone: ZoneInfo,
        *,
        window_minutes: int = 15,
        scan_interval_seconds: int = DEFAULT_SCAN_INTERVAL_SECONDS,
        min_bars_for_features: int = 60,
        warmup_bars: int = 120,
        terminal_tolerance_seconds: float = DEFAULT_TERMINAL_TOLERANCE_SECONDS,
        settlement_rule: SettlementRule = SettlementRule.PROXY_TERMINAL_ABOVE_REFERENCE,
        paranoid: bool = False,
    ) -> None:
        self.frames = frames
        self.timezone = timezone
        self.window_minutes = window_minutes
        self.scan_interval_seconds = scan_interval_seconds
        self.min_bars_for_features = min_bars_for_features
        self.warmup_bars = warmup_bars
        self.terminal_tolerance_seconds = terminal_tolerance_seconds
        self.settlement_rule = settlement_rule
        #: When True every view self-verifies. Slower; used by the leakage tests.
        self.paranoid = paranoid
        #: Causal indicators, computed once per asset. See features.py for why
        #: precomputation does not weaken the leakage barrier, and for the test
        #: that proves it.
        self._indicators: dict[str, pd.DataFrame] = {}

    def indicators_for(self, asset: str) -> Optional[pd.DataFrame]:
        if asset not in self._indicators:
            frame = self.frames.get(asset)
            self._indicators[asset] = (
                None if frame is None else precompute_indicators(frame)
            )
        return self._indicators[asset]

    # ------------------------------------------------------------------
    # Window enumeration
    # ------------------------------------------------------------------

    def windows_for(self, asset: str) -> list[ContractWindow]:
        """Every complete window this asset's data can support.

        Both ends are trimmed deliberately:
          * the first ``warmup_bars`` are reserved so that early contracts are
            not scored on features computed from a near-empty history;
          * the final window is excluded unless a bar exists at or after its
            end, because settlement needs a terminal observation. Including it
            would add contracts that can only ever be UNRESOLVED.
        """
        frame = self.frames.get(asset)
        if frame is None or len(frame) <= self.warmup_bars:
            return []

        usable = frame.iloc[self.warmup_bars :]
        if usable.empty:
            return []

        first = usable.index[0].to_pydatetime()
        last = frame.index[-1].to_pydatetime()

        window = ContractWindow.for_instant(first, self.timezone, self.window_minutes)
        if window.start_utc < first:
            window = window.next_window()   # never a partially-covered first window

        windows: list[ContractWindow] = []
        while window.end_utc <= last:
            windows.append(window)
            window = window.next_window()
        return windows

    def scan_times(self, window: ContractWindow) -> Iterator[datetime]:
        """The fixed forward scan grid for one window."""
        moment = window.start_utc
        step = timedelta(seconds=self.scan_interval_seconds)
        while moment < window.end_utc:
            yield moment
            moment += step

    # ------------------------------------------------------------------
    # Replay
    # ------------------------------------------------------------------

    def replay_contract(
        self,
        asset: str,
        window: ContractWindow,
        strategy: BacktestStrategy,
        *,
        keep_scans: bool = False,
    ) -> ContractReplay:
        """Replay one contract. Never raises for data reasons."""
        if not strategy.evaluable:
            raise StrategyNotEvaluable(strategy.not_evaluable_reason)

        replay = ContractReplay(asset=asset, window=window, strategy=strategy.name)
        frame = self.frames.get(asset)

        if frame is None or len(frame) == 0:
            replay.data_quality_status = "NO_DATA"
            replay.skip_reason = "no bars for asset"
            return replay

        # -- reference, from contract-start information only ----------------
        reference = derive_proxy_reference(frame, window)
        if reference is None:
            replay.data_quality_status = "NO_START_REFERENCE"
            replay.skip_reason = "no bar inside the window to anchor the reference"
            return replay

        replay.reference = reference.price
        replay.reference_bar_utc = reference.bar_utc
        # Requirement 1: these are pinned False. There is no path that sets them
        # True, because verification requires a real contract specification.
        replay.reference_verified = False
        replay.economics_available = False

        # -- forward walk ---------------------------------------------------
        indicators = self.indicators_for(asset)
        strategy.reset()
        for scan_utc in self.scan_times(window):
            view = HistoricalMarketView(frame, scan_utc, asset=asset)
            if self.paranoid:
                view.assert_no_lookahead()

            features = compute_features(
                view,
                window,
                reference,
                indicators=indicators,
                min_bars=self.min_bars_for_features,
            )
            replay.scan_count += 1

            if features is None:
                if keep_scans:
                    replay.scans.append(
                        ScanRecord(
                            scan_utc=scan_utc,
                            seconds_remaining=window.seconds_remaining(scan_utc),
                            elapsed_fraction=window.elapsed_fraction(scan_utc),
                            spot=view.spot,
                            action=Action.WAIT.value,
                            p_yes=None,
                            reason="insufficient history for features",
                            features_ok=False,
                        )
                    )
                continue

            decision: Decision = strategy.decide(features)

            if keep_scans:
                replay.scans.append(
                    ScanRecord(
                        scan_utc=scan_utc,
                        seconds_remaining=features["seconds_remaining"],
                        elapsed_fraction=features["elapsed_fraction"],
                        spot=features["spot"],
                        action=decision.action.value,
                        p_yes=decision.p_yes,
                        reason=decision.reason,
                        features_ok=True,
                    )
                )

            if decision.action.is_entry:
                replay.entered = True
                replay.entry_side = decision.action.side
                replay.entry_timestamp = scan_utc
                replay.entry_spot = features["spot"]
                replay.entry_p_yes = decision.p_yes
                replay.entry_reason = decision.reason
                # FROZEN: deep copy, never rewritten. See module docstring.
                replay.entry_features = copy.deepcopy(features)
                replay.decision = decision.action.value
                break

            if decision.action is Action.NO_TRADE:
                replay.decision = Action.NO_TRADE.value
                break

        if not replay.entered and replay.decision != Action.NO_TRADE.value:
            replay.decision = Action.NO_TRADE.value

        # -- settlement: strictly after the scan loop ------------------------
        self._settle(replay, frame, window, reference)
        return replay

    def _settle(
        self,
        replay: ContractReplay,
        frame: pd.DataFrame,
        window: ContractWindow,
        reference: ProxyReference,
    ) -> None:
        """Resolve the contract. Runs only after all scanning is complete."""
        observation = find_terminal_price(
            frame, window.end_utc, tolerance_seconds=self.terminal_tolerance_seconds
        )
        if observation is None:
            replay.data_quality_status = "NO_TERMINAL_BAR"
            replay.skip_reason = "no bar within tolerance of the resolution instant"
            return

        replay.terminal_price = observation.price
        replay.terminal_source = f"{observation.source}(lag={observation.lag_seconds:+.0f}s)"
        replay.terminal_bar_utc = observation.bar_utc

        outcome = self.settlement_rule.settle(observation.price, reference.price)
        if outcome is None:
            replay.data_quality_status = "UNSETTLEABLE"
            replay.skip_reason = f"rule {self.settlement_rule.value} could not settle"
            return

        replay.outcome_yes = outcome
        replay.outcome_label = "YES" if outcome else "NO"
        replay.usable = True

        # Correctness is defined only when a side was taken. A NO TRADE is
        # neither correct nor incorrect; scoring it as a loss would corrupt
        # every accuracy statistic downstream.
        if replay.entry_side in ("YES", "NO"):
            replay.prediction_correct = int(replay.entry_side == replay.outcome_label)

    # ------------------------------------------------------------------

    def replay_all(
        self,
        strategy: BacktestStrategy,
        assets: Optional[list[str]] = None,
        *,
        limit: Optional[int] = None,
    ) -> list[ContractReplay]:
        """Replay every contract for every asset, chronologically per asset."""
        results: list[ContractReplay] = []
        for asset in (assets if assets is not None else sorted(self.frames)):
            windows = self.windows_for(asset)
            if limit is not None:
                windows = windows[:limit]
            for window in windows:
                results.append(self.replay_contract(asset, window, strategy))
        return results
