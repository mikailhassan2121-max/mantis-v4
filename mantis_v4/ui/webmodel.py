"""Serialise the immutable ``UiSnapshot`` into the JSON the web shell consumes.

This module is the web equivalent of ``panels.py``: it draws nothing and it
decides nothing. Every value it emits is either copied verbatim from a backend
``LiveSnapshot`` field that ``state.py`` already captured, or is one of the two
display quantities ``AssetView`` already exposes and that Phase 9 documented --
``seconds_remaining`` (``window_end - now`` against the backend's own
authoritative resolution timestamp) and ``elapsed_fraction``.

There is no arithmetic here beyond ``isoformat()`` and ``float()``. In
particular the browser is never sent a probability, an edge or an expected value
that this process computed; it is sent the ones the engine published.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from . import hostmetrics, theme
from .state import AssetView, LogEntry, SystemError, SystemStatus, UiSnapshot

UTC = timezone.utc


def _stamp(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if isinstance(value, datetime) else None


def _decision(value: Optional[str]) -> dict:
    """Describe a backend decision string for the stylesheet.

    The UI classifies nothing: it looks the decision string up in the same
    table the Rich renderer uses, so both front ends agree by construction.
    """
    style = theme.decision_style(value)
    return {
        "key": style.key,
        "label": style.label,
        "glyph": style.glyph,
        "emphasis": style.emphasis,
        "raw": value or "",
    }


def asset_payload(view: AssetView, now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(UTC)
    return {
        "asset": view.asset,
        "short": view.asset.replace("-USD", ""),
        "contract_id": view.contract_id,
        "window_start": _stamp(view.window_start),
        "window_end": _stamp(view.window_end),
        "scan_timestamp": _stamp(view.scan_timestamp),
        # Sent so the browser can tick between scans against the backend's own
        # window_end rather than inventing a contract boundary of its own.
        "seconds_remaining": view.seconds_remaining(now),
        "elapsed_fraction": view.elapsed_fraction(now),

        "reference": view.reference,
        "reference_status": view.reference_status,
        "current_price": view.current_price,
        "buffer": view.buffer,

        "p_yes": view.p_yes,
        "p_no": view.p_no,
        "predicted_side": view.predicted_side,
        "conservative_bound": view.conservative_bound,
        "fragility": view.fragility,
        "disagreement": view.disagreement,
        "crossing_probability": view.crossing_probability,
        "reference_crossings": view.reference_crossings,
        "volatility_estimate": view.volatility_estimate,

        "classification": _decision(view.classification_state),
        "classification_reason": view.classification_reason,
        "classification_reason_text": theme.reason_text(view.classification_reason),
        "final": _decision(view.final_decision),
        "final_reason": view.final_reason,
        "final_reason_text": theme.reason_text(view.final_reason),

        "yes_bid": view.yes_bid,
        "yes_ask": view.yes_ask,
        "no_bid": view.no_bid,
        "no_ask": view.no_ask,
        "quote_status": view.quote_status,
        "quote_age": view.quote_age,
        "break_even": view.break_even,
        "model_edge": view.model_edge,
        "lcb_edge": view.lcb_edge,
        "point_ev": view.point_ev,
        "lcb_ev": view.lcb_ev,
        "expected_return_on_cost": view.expected_return_on_cost,
        "ev_status": view.ev_status,
        "fees_status": view.fees_status,
        "slippage_status": view.slippage_status,
        "economics_available": view.economics_available,

        "data_age_seconds": view.data_age_seconds,
        "fetch_latency_seconds": view.fetch_latency_seconds,
        "provider_mode": view.provider_mode,
        "quality_reason": view.quality_reason,
        "volatility_regime": view.volatility_regime,
        "buffer_z": view.buffer_z,
        "model_version": view.model_version,
        "config_hash": view.config_hash,
        "git_commit": view.git_commit,

        "has_data": view.has_data,
        "synthetic": view.synthetic,
        "spark": list(view.spark),
    }


def status_payload(status: SystemStatus, clients: Optional[int] = None) -> dict:
    data = asdict(status) if is_dataclass(status) else dict(status)
    for key, value in list(data.items()):
        if isinstance(value, datetime):
            data[key] = value.isoformat()
    data["host"] = hostmetrics.host_payload(clients)
    return data


def event_payload(entry: LogEntry) -> dict:
    return {
        "timestamp": _stamp(entry.timestamp),
        "severity": entry.severity,
        "source": entry.source,
        "message": entry.message,
        "detail": entry.detail,
    }


def error_payload(error: Optional[SystemError], developer_mode: bool = False) -> Optional[dict]:
    if error is None:
        return None
    return {
        "timestamp": _stamp(error.timestamp),
        "provider": error.provider,
        "component": error.component,
        "message": error.message,
        "recovery": error.recovery,
        # The traceback stays in the diagnostics log unless the operator asked
        # for it, exactly as the Rich error panel behaves.
        "traceback": error.traceback_text if developer_mode else "",
    }


def branding_payload() -> dict:
    return {
        "system_owner": theme.SYSTEM_OWNER,
        "system_owner_short": theme.SYSTEM_OWNER_SHORT,
        "system_owner_line": theme.SYSTEM_OWNER_LINE,
        "product": theme.PRODUCT_NAME,
        "product_spaced": theme.PRODUCT_SHORT,
        "expansion": theme.PRODUCT_EXPANSION,
        "advisory": theme.ADVISORY_BANNER,
        "software_version": theme.SOFTWARE_VERSION,
    }


def presentation_payload(config: Any) -> dict:
    """Only the presentation switches the browser needs. No quant settings."""
    return {
        "scanlines": bool(getattr(config, "scanlines_enabled", True)),
        "background_grid": bool(getattr(config, "background_grid_enabled", True)),
        "boot_enabled": bool(getattr(config, "boot_sequence_enabled", True)),
        "boot_duration": float(getattr(config, "boot_duration", 15.0)),
        "brand_prelude_enabled": bool(getattr(config, "brand_prelude_enabled", True)),
        "brand_prelude_duration_seconds": float(getattr(config, "brand_prelude_duration_seconds", 13.0)),
        "technical_boot_duration_seconds": float(getattr(config, "technical_boot_duration_seconds", 8.5)),
        "startup_settle_seconds": float(getattr(config, "startup_settle_seconds", 0.75)),
        "startup_snapshot_timeout_seconds": float(getattr(config, "startup_snapshot_timeout_seconds", 7.0)),
        "boot_audio": bool(getattr(config, "boot_audio_enabled", True)),
        "audio_enabled": bool(getattr(config, "audio_enabled", True)),
        "master_volume": float(getattr(config, "master_volume", 0.7)),
        "refresh_rate": float(getattr(config, "ui_refresh_rate", 4.0)),
        "diagnostics": bool(getattr(config, "show_advanced_diagnostics", False)),
        "operator_diagnostics": bool(getattr(config, "operator_diagnostics", False)),
        "demo_mode": bool(getattr(config, "demo_mode", False)),
        "developer_mode": bool(getattr(config, "developer_mode", False)),
        "event_log_rows": int(getattr(config, "event_log_visible_rows", 8)),
    }


def snapshot_payload(snapshot: UiSnapshot, config: Any,
                     now: Optional[datetime] = None,
                     clients: Optional[int] = None) -> dict:
    """The whole screen, as plain JSON-safe data."""
    now = now or datetime.now(UTC)
    developer_mode = bool(getattr(config, "developer_mode", False))
    return {
        "type": "snapshot",
        "server_time_utc": now.isoformat(),
        "focus": snapshot.focus,
        "primary_selection": snapshot.primary_selection,
        "operator_state": snapshot.operator_state,
        "demo_mode": snapshot.demo_mode,
        "status": status_payload(snapshot.status, clients),
        "assets": [asset_payload(view, now) for view in snapshot.assets],
        "events": [event_payload(entry) for entry in snapshot.events],
        "forward": snapshot.forward,
        "historical": snapshot.historical,
        "error": error_payload(snapshot.last_error, developer_mode),
        "reason_text": dict(theme.REASON_TEXT),
        "branding": branding_payload(),
        "presentation": presentation_payload(config),
    }
