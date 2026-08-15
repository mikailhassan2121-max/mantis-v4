"""Thread-safe presentation state.

This module is the only bridge between the quantitative loop and the screen.

Rules it enforces:

1.  Every displayed number is copied out of a backend ``LiveSnapshot`` (or an
    explicit "no data" marker). Nothing here recomputes probability, EV,
    edges, thresholds or contract boundaries.
2.  The countdown interpolates against the backend's own authoritative
    ``window_end`` timestamp. The UI never derives a contract boundary.
3.  All mutation happens under one lock and the renderer only ever reads an
    immutable copy, so a slow render cannot interleave with a scan write.
4.  In-memory history is bounded. The append-only forward logs on disk remain
    the complete record.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Optional

UTC = timezone.utc

MAX_SPARK_POINTS = 60


def _parse(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@dataclass(frozen=True)
class AssetView:
    """One asset card. Every field mirrors a backend value verbatim."""

    asset: str
    contract_id: Optional[str] = None
    window_start: Optional[datetime] = None
    window_end: Optional[datetime] = None
    scan_timestamp: Optional[datetime] = None

    reference: Optional[float] = None
    reference_status: Optional[str] = None
    current_price: Optional[float] = None
    buffer: Optional[float] = None

    p_yes: Optional[float] = None
    p_no: Optional[float] = None
    predicted_side: Optional[str] = None
    conservative_bound: Optional[float] = None
    fragility: Optional[float] = None
    disagreement: Optional[float] = None
    crossing_probability: Optional[float] = None
    reference_crossings: Optional[int] = None
    volatility_estimate: Optional[float] = None

    classification_state: Optional[str] = None
    classification_reason: Optional[str] = None
    final_decision: str = "DATA HOLD"
    final_reason: str = "AWAITING_FIRST_SCAN"

    yes_bid: Optional[float] = None
    yes_ask: Optional[float] = None
    no_bid: Optional[float] = None
    no_ask: Optional[float] = None
    quote_status: str = "UNAVAILABLE"
    quote_age: Optional[float] = None
    break_even: Optional[float] = None
    model_edge: Optional[float] = None
    lcb_edge: Optional[float] = None
    point_ev: Optional[float] = None
    lcb_ev: Optional[float] = None
    expected_return_on_cost: Optional[float] = None
    ev_status: str = "ECONOMICS_UNAVAILABLE"
    fees_status: str = "UNKNOWN_FEES"
    slippage_status: str = "SLIPPAGE_NOT_MODELED"

    data_age_seconds: Optional[float] = None
    fetch_latency_seconds: Optional[float] = None
    provider_mode: Optional[str] = None
    quality_reason: Optional[str] = None
    volatility_regime: Optional[str] = None
    git_commit: Optional[str] = None
    config_hash: Optional[str] = None
    model_version: Optional[str] = None

    has_data: bool = False
    synthetic: bool = False
    entered_at: Optional[datetime] = None   # when the current ENTER state began
    spark: tuple[float, ...] = ()

    # -- derived-for-display only -------------------------------------------

    def seconds_remaining(self, now: Optional[datetime] = None) -> Optional[float]:
        """Live countdown against the backend's authoritative window end."""
        if self.window_end is None:
            return None
        now = now or datetime.now(UTC)
        return max(0.0, (self.window_end - now).total_seconds())

    def elapsed_fraction(self, now: Optional[datetime] = None) -> Optional[float]:
        if self.window_start is None or self.window_end is None:
            return None
        span = (self.window_end - self.window_start).total_seconds()
        if span <= 0:
            return None
        now = now or datetime.now(UTC)
        return min(1.0, max(0.0, (now - self.window_start).total_seconds() / span))

    @property
    def buffer_z(self) -> Optional[float]:
        """Buffer normalised by window volatility, for the diagnostics view.

        This is a display normalisation of three values the backend already
        publishes on the snapshot (``buffer``, ``reference``,
        ``volatility_estimate``). It mirrors the expression ``ForwardEngine``
        feeds into ``RiskDiagnostics.buffer_z`` and is shown for inspection
        only -- no UI decision reads it. ``test_phase9_ui`` pins it against the
        engine's own value so the two cannot drift apart silently.
        """
        if self.buffer is None or not self.reference or not self.volatility_estimate:
            return None
        if self.volatility_estimate <= 0:
            return None
        return self.buffer / self.reference / self.volatility_estimate

    @property
    def is_entering(self) -> bool:
        return self.final_decision in ("ENTER YES", "ENTER NO")

    @property
    def economics_available(self) -> bool:
        return self.ev_status not in ("ECONOMICS_UNAVAILABLE", "UNAVAILABLE", "")

    @property
    def stale(self) -> bool:
        return self.final_decision == "DATA HOLD"


def view_from_snapshot(
    snapshot: Any,
    previous: Optional[AssetView] = None,
    synthetic: bool = False,
) -> AssetView:
    """Map a backend ``LiveSnapshot`` onto a card. Pure field copying."""
    metadata = getattr(snapshot, "metadata", None) or {}
    spark = tuple(previous.spark) if previous else ()
    p_yes = getattr(snapshot, "p_yes", None)
    if p_yes is not None:
        spark = (spark + (float(p_yes),))[-MAX_SPARK_POINTS:]

    decision = getattr(snapshot, "final_decision", "DATA HOLD")
    entered_at = None
    if decision in ("ENTER YES", "ENTER NO"):
        same_contract = previous is not None and previous.contract_id == getattr(snapshot, "contract_id", None)
        if previous is not None and previous.is_entering and same_contract and previous.entered_at:
            entered_at = previous.entered_at
        else:
            entered_at = _parse(getattr(snapshot, "timestamp_utc", None))

    return AssetView(
        asset=getattr(snapshot, "asset", "UNKNOWN"),
        contract_id=getattr(snapshot, "contract_id", None),
        window_start=_parse(getattr(snapshot, "window_start", None)),
        window_end=_parse(getattr(snapshot, "window_end", None)),
        scan_timestamp=_parse(getattr(snapshot, "timestamp_utc", None)),
        reference=getattr(snapshot, "reference", None),
        reference_status=getattr(snapshot, "reference_status", None),
        current_price=getattr(snapshot, "current_price", None),
        buffer=getattr(snapshot, "buffer", None),
        p_yes=p_yes,
        p_no=getattr(snapshot, "p_no", None),
        predicted_side=getattr(snapshot, "predicted_side", None),
        conservative_bound=getattr(snapshot, "conservative_bound", None),
        fragility=getattr(snapshot, "fragility", None),
        disagreement=getattr(snapshot, "disagreement", None),
        crossing_probability=getattr(snapshot, "crossing_probability", None),
        reference_crossings=getattr(snapshot, "reference_crossings", None),
        volatility_estimate=getattr(snapshot, "volatility_estimate", None),
        classification_state=getattr(snapshot, "phase6_state", None),
        classification_reason=getattr(snapshot, "phase6_reason", None),
        final_decision=decision,
        final_reason=getattr(snapshot, "final_reason", ""),
        yes_bid=getattr(snapshot, "yes_bid", None),
        yes_ask=getattr(snapshot, "yes_ask", None),
        no_bid=getattr(snapshot, "no_bid", None),
        no_ask=getattr(snapshot, "no_ask", None),
        quote_status=getattr(snapshot, "quote_status", "UNAVAILABLE"),
        quote_age=getattr(snapshot, "quote_age", None),
        break_even=getattr(snapshot, "break_even", None),
        model_edge=getattr(snapshot, "model_edge", None),
        lcb_edge=getattr(snapshot, "lcb_edge", None),
        point_ev=getattr(snapshot, "point_ev", None),
        lcb_ev=getattr(snapshot, "lcb_ev", None),
        expected_return_on_cost=getattr(snapshot, "expected_return_on_cost", None),
        ev_status=getattr(snapshot, "ev_status", "ECONOMICS_UNAVAILABLE"),
        fees_status=getattr(snapshot, "fees_status", "UNKNOWN_FEES"),
        slippage_status=getattr(snapshot, "slippage_status", "SLIPPAGE_NOT_MODELED"),
        data_age_seconds=getattr(snapshot, "data_age_seconds", None),
        fetch_latency_seconds=getattr(snapshot, "fetch_latency_seconds", None),
        provider_mode=metadata.get("provider_mode"),
        quality_reason=metadata.get("quality_reason"),
        volatility_regime=metadata.get("volatility_regime"),
        git_commit=metadata.get("git_commit"),
        config_hash=metadata.get("config_hash"),
        model_version=metadata.get("model_version"),
        has_data=True,
        synthetic=synthetic,
        entered_at=entered_at,
        spark=spark,
    )


# ---------------------------------------------------------------------------
# Event log
# ---------------------------------------------------------------------------

SEVERITY_ORDER = {"INFO": 0, "NOTICE": 1, "ACTION": 2, "WARNING": 3, "CRITICAL": 4}


@dataclass(frozen=True)
class LogEntry:
    timestamp: datetime
    severity: str
    source: str
    message: str
    detail: str = ""


@dataclass(frozen=True)
class SystemStatus:
    """Header and provider-panel facts. Copied from backend/provider objects."""

    run_id: str = "UNKNOWN"
    software_version: str = "MANTIS_V4_PHASE9"
    model_version: str = "UNKNOWN"
    policy_name: str = "UNKNOWN"
    git_commit: str = "UNKNOWN"
    config_hash: str = "UNKNOWN"
    started_at: Optional[datetime] = None
    local_timezone: str = "UTC"

    underlying_provider: str = "UNKNOWN"
    underlying_state: str = "UNAVAILABLE"
    underlying_detail: str = ""
    underlying_last_success: Optional[datetime] = None
    underlying_successes: int = 0
    underlying_failures: int = 0

    webull_status: str = "AUTH_NOT_CONFIGURED"
    economics_provider: str = "underlying-proxy"
    economics_status: str = "DISABLED"
    reference_status: str = "PROXY_UNVERIFIED"
    quote_status: str = "UNAVAILABLE"

    latency_seconds: Optional[float] = None
    scan_count: int = 0
    error_count: int = 0
    last_scan_utc: Optional[datetime] = None
    next_scan_utc: Optional[datetime] = None
    audio_enabled: bool = False
    voice_enabled: bool = False
    forward_logger_status: str = "READY"
    http_server_status: str = "NOT STARTED"
    browser_shell_status: str = "NOT STARTED"
    demo_mode: bool = False
    observation_only: bool = True


@dataclass(frozen=True)
class SystemError:
    """Operator-facing failure summary. The traceback goes to the disk log."""

    timestamp: datetime
    provider: str
    component: str
    message: str
    recovery: str
    traceback_text: str = ""


@dataclass(frozen=True)
class UiSnapshot:
    """Immutable read model handed to the renderer."""

    assets: tuple[AssetView, ...]
    status: SystemStatus
    events: tuple[LogEntry, ...]
    forward: Optional[dict]
    historical: Optional[dict]
    last_error: Optional[SystemError]
    focus: Optional[str]
    demo_mode: bool = False


class CommandCenterState:
    """Mutable state owned by the quant thread, read by the render thread."""

    def __init__(self, assets, config, status: Optional[SystemStatus] = None):
        self._lock = threading.RLock()
        self._order = list(assets)
        self._config = config
        self._assets = {a: AssetView(asset=a) for a in self._order}
        self._events: deque[LogEntry] = deque(maxlen=int(config.event_log_length))
        self._status = status or SystemStatus(demo_mode=config.demo_mode)
        self._forward: Optional[dict] = None
        self._historical: Optional[dict] = None
        self._last_error: Optional[SystemError] = None

    # -- writes (quant thread) ---------------------------------------------

    def update_from_snapshot(self, snapshot: Any, synthetic: bool = False) -> AssetView:
        with self._lock:
            asset = getattr(snapshot, "asset", None)
            if asset is None:
                raise ValueError("snapshot has no asset")
            if asset not in self._assets:
                self._order.append(asset)
            view = view_from_snapshot(snapshot, self._assets.get(asset), synthetic=synthetic)
            self._assets[asset] = view
            return view

    def mark_no_data(self, asset: str, reason: str = "UNDERLYING_DATA_UNAVAILABLE") -> None:
        """Underlying data missing for this asset: hold, never show stale values.

        Presentation only. It writes nothing to the forward store, because no
        observation was produced by the engine for this scan.
        """
        with self._lock:
            if asset not in self._assets:
                self._order.append(asset)
            previous = self._assets.get(asset, AssetView(asset=asset))
            self._assets[asset] = AssetView(
                asset=asset,
                contract_id=previous.contract_id,
                window_start=previous.window_start,
                window_end=previous.window_end,
                final_decision="DATA HOLD",
                final_reason=reason,
                classification_state="DATA HOLD",
                classification_reason=reason,
                has_data=False,
                synthetic=previous.synthetic,
                spark=previous.spark,
            )

    def clear_window(self, contract_id: Optional[str]) -> None:
        """Rollover: drop per-window values so a new window visibly initialises."""
        with self._lock:
            for asset, view in list(self._assets.items()):
                if view.contract_id == contract_id:
                    continue
                self._assets[asset] = AssetView(
                    asset=asset,
                    contract_id=contract_id,
                    final_decision="WAIT",
                    final_reason="EARLY_INSUFFICIENT_INFORMATION",
                    has_data=False,
                    synthetic=view.synthetic,
                )

    def log(self, severity: str, source: str, message: str, detail: str = "",
            timestamp: Optional[datetime] = None) -> LogEntry:
        entry = LogEntry(timestamp or datetime.now(UTC), severity, source, message, detail)
        with self._lock:
            self._events.append(entry)
        return entry

    def set_status(self, **changes) -> SystemStatus:
        with self._lock:
            self._status = replace(self._status, **changes)
            return self._status

    def record_scan(self, **changes) -> SystemStatus:
        """Increment the scan counter and apply status changes in one lock pass.

        The scanner calls this per asset per scan, so it must not copy the
        event log the way ``snapshot()`` does.
        """
        with self._lock:
            self._status = replace(self._status, scan_count=self._status.scan_count + 1,
                                   **changes)
            return self._status

    def set_forward(self, report: Optional[dict], historical: Optional[dict] = None) -> None:
        with self._lock:
            self._forward = report
            if historical is not None:
                self._historical = historical

    def set_error(self, error: Optional[SystemError]) -> None:
        with self._lock:
            self._last_error = error
            if error is not None:
                self._status = replace(self._status, error_count=self._status.error_count + 1)

    # -- reads (render thread) ---------------------------------------------

    def focus_asset(self) -> Optional[str]:
        """Focused card.

        There is no validated cross-asset ranking, so none is invented. The
        configured asset holds focus unless one or more assets are in an ENTER
        state, in which case the longest-standing ENTER takes focus, tie-broken
        by configured asset order. No quality comparison is performed.
        """
        with self._lock:
            entering = [self._assets[a] for a in self._order
                        if a in self._assets and self._assets[a].is_entering]
            if entering:
                entering.sort(key=lambda v: (
                    v.entered_at or datetime.max.replace(tzinfo=UTC),
                    self._order.index(v.asset),
                ))
                return entering[0].asset
            default = self._config.default_focused_asset
            if default in self._assets:
                return default
            return self._order[0] if self._order else None

    def snapshot(self) -> UiSnapshot:
        with self._lock:
            return UiSnapshot(
                assets=tuple(self._assets[a] for a in self._order if a in self._assets),
                status=self._status,
                events=tuple(self._events),
                forward=dict(self._forward) if self._forward else None,
                historical=dict(self._historical) if self._historical else None,
                last_error=self._last_error,
                focus=self.focus_asset(),
                demo_mode=self._status.demo_mode,
            )

    def view(self, asset: str) -> Optional[AssetView]:
        with self._lock:
            return self._assets.get(asset)
