"""UI_DEMO_MODE: synthetic states for evaluating the interface.

Safety property, enforced structurally rather than by convention: this module
imports only ``LiveSnapshot`` (a plain dataclass) and the event-hook types from
the forward package. It never imports, constructs, or receives ``ForwardStore``
or ``ForwardEngine``, so there is no code path by which a demo state can reach
the forward-validation logs.

Every synthetic snapshot carries ``run_id="DEMO"`` and ``metadata["synthetic"]
= True`` and every card is labelled DEMO / SYNTHETIC DATA on screen.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone

from ..forward.events import AppEvent, EventBus, EventType
from ..forward.models import LiveSnapshot

UTC = timezone.utc

DEMO_RUN_ID = "DEMO"

BASE_PRICE = {
    "BTC-USD": 118_400.0,
    "ETH-USD": 4_180.0,
    "SOL-USD": 214.0,
    "XRP-USD": 2.94,
    "ADA-USD": 0.86,
}

# A fixed tour of decision states so every visual treatment can be inspected.
SCRIPT = [
    ("WAIT", "EARLY_INSUFFICIENT_INFORMATION", "WAIT", 0.63),
    ("WAIT", "PROBABILITY_TOO_LOW", "WAIT", 0.88),
    ("WAIT", "FRAGILITY_TOO_HIGH", "WAIT", 0.965),
    ("ENTER YES", "ELIGIBLE", "WAIT", 0.968),
    ("ENTER YES", "ELIGIBLE", "ENTER YES", 0.972),
    ("ENTER NO", "ELIGIBLE", "ENTER NO", 0.031),
    ("ENTER NO", "ELIGIBLE", "NO TRADE THIS CONTRACT", 0.028),
    ("DATA HOLD", "STALE_DATA", "DATA HOLD", 0.5),
    ("NO TRADE THIS CONTRACT", "CHOP_UNSTABLE", "NO TRADE THIS CONTRACT", 0.51),
]


class DemoFeed:
    """Deterministic synthetic snapshot generator."""

    def __init__(self, assets, seed: int = 20260814, window_minutes: int = 15):
        self.assets = list(assets)
        self.window_minutes = window_minutes
        self.random = random.Random(seed)
        self.tick = 0

    def _window(self, now: datetime):
        span = self.window_minutes * 60
        start_epoch = (int(now.timestamp()) // span) * span
        start = datetime.fromtimestamp(start_epoch, tz=UTC)
        return start, start + timedelta(seconds=span)

    def snapshots(self, now: datetime | None = None) -> list[LiveSnapshot]:
        now = now or datetime.now(UTC)
        start, end = self._window(now)
        contract_id = f"DEMO-{int(start.timestamp())}"
        seconds = max(0.0, (end - now).total_seconds())
        out = []
        for index, asset in enumerate(self.assets):
            phase, reason, final, p_yes = SCRIPT[(self.tick + index) % len(SCRIPT)]
            base = BASE_PRICE.get(asset, 100.0)
            drift = math.sin((self.tick + index) / 3.0) * base * 0.0012
            reference = base
            spot = base + drift
            economics = final in ("ENTER YES", "ENTER NO", "NO TRADE THIS CONTRACT")
            side = "YES" if p_yes >= 0.5 else "NO"
            confidence = max(p_yes, 1 - p_yes)
            ask = round(0.62 + 0.2 * self.random.random(), 3) if economics else None
            out.append(LiveSnapshot(
                run_id=DEMO_RUN_ID,
                observation_id=f"demo-{self.tick}-{asset}",
                timestamp_utc=now.isoformat(),
                timestamp_local=now.isoformat(),
                asset=asset,
                contract_id=contract_id,
                window_start=start.isoformat(),
                window_end=end.isoformat(),
                seconds_remaining=seconds,
                reference=reference,
                reference_status="PROXY_UNVERIFIED",
                current_price=spot,
                buffer=spot - reference,
                volatility_estimate=0.0009 + 0.0004 * self.random.random(),
                p_yes=p_yes,
                p_no=1 - p_yes,
                predicted_side=side,
                conservative_bound=max(0.0, confidence - 0.045),
                fragility=18 + 55 * self.random.random(),
                disagreement=0.004 + 0.03 * self.random.random(),
                crossing_probability=0.05 + 0.25 * self.random.random(),
                reference_crossings=(self.tick + index) % 4,
                phase6_state=phase,
                phase6_reason=reason,
                final_decision=final,
                final_reason=reason if final == phase else _final_reason(final),
                data_age_seconds=120.0 if final == "DATA HOLD" else 4.0 + 6 * self.random.random(),
                fetch_latency_seconds=0.18 + 0.4 * self.random.random(),
                next_scan_utc=(now + timedelta(seconds=5)).isoformat(),
                yes_bid=(ask - 0.02) if ask else None,
                yes_ask=ask,
                no_bid=(0.94 - ask) if ask else None,
                no_ask=(0.98 - ask) if ask else None,
                quote_timestamp=now.isoformat() if economics else None,
                quote_age=2.1 if economics else None,
                quote_status="ROBUST_POSITIVE_EV" if economics else "UNAVAILABLE",
                break_even=ask if ask else None,
                model_edge=(confidence - ask) if ask else None,
                lcb_edge=(confidence - 0.045 - ask) if ask else None,
                point_ev=(confidence - ask) if ask else None,
                lcb_ev=(confidence - 0.045 - ask) if ask else None,
                expected_return_on_cost=((confidence - ask) / ask) if ask else None,
                fees_status="UNKNOWN_FEES",
                slippage_status="SLIPPAGE_NOT_MODELED",
                ev_status="ROBUST_POSITIVE_EV" if final in ("ENTER YES", "ENTER NO")
                          else "NEGATIVE_EV" if economics else "ECONOMICS_UNAVAILABLE",
                metadata={"synthetic": True, "demo": True, "provider_mode": "demo",
                          "quality_reason": "SYNTHETIC", "volatility_regime": "DEMO",
                          "git_commit": "DEMO", "config_hash": "DEMO",
                          "model_version": "DEMO_NORMAL_Z"},
            ))
        self.tick += 1
        return out


def _final_reason(final: str) -> str:
    return {
        "WAIT": "CONTRACT_QUOTE_UNAVAILABLE",
        "ENTER YES": "ROBUST_POSITIVE_EV",
        "ENTER NO": "ROBUST_POSITIVE_EV",
        "NO TRADE THIS CONTRACT": "NEGATIVE_EV",
        "DATA HOLD": "STALE_DATA",
    }.get(final, "")


def emit_demo_events(bus: EventBus, snapshots: list[LiveSnapshot]) -> None:
    """Drive the same Phase 8 hooks with synthetic payloads.

    The bus is a pub/sub object with no persistence. Payloads are marked
    synthetic so anything downstream that logs them says so.
    """
    for snapshot in snapshots:
        payload = {"asset": snapshot.asset, "reason": snapshot.final_reason,
                   "synthetic": True, "contract_id": snapshot.contract_id}
        if snapshot.final_decision == "ENTER YES":
            bus.emit(AppEvent(EventType.ENTRY_YES, snapshot.timestamp_utc,
                              {**payload, "side": "YES",
                               "model_probability": snapshot.p_yes,
                               "seconds_remaining": snapshot.seconds_remaining}))
        elif snapshot.final_decision == "ENTER NO":
            bus.emit(AppEvent(EventType.ENTRY_NO, snapshot.timestamp_utc,
                              {**payload, "side": "NO",
                               "model_probability": snapshot.p_no,
                               "seconds_remaining": snapshot.seconds_remaining}))
        elif snapshot.final_decision == "DATA HOLD":
            bus.emit(AppEvent(EventType.DATA_HOLD, snapshot.timestamp_utc, payload))
        else:
            bus.emit(AppEvent(EventType.WAIT, snapshot.timestamp_utc, payload))


def emit_demo_lifecycle(bus: EventBus, snapshots: list[LiveSnapshot], tick: int) -> None:
    """Cycle the surrounding system events so every treatment can be seen.

    Contract rollover and resolution are ordinarily minutes apart; the showcase
    produces them on a short cycle. Payloads stay marked synthetic and the bus
    still has no persistence, so none of this can reach the forward store.
    """
    if not snapshots:
        return
    if tick and tick % 12 == 0:
        first = snapshots[0]
        bus.emit(AppEvent(EventType.ROLLOVER, first.timestamp_utc,
                          {"synthetic": True, "asset": first.asset,
                           "to": first.contract_id, "reason": "CONTRACT_ROLLOVER"}))
    if tick and tick % 7 == 0:
        subject = snapshots[tick % len(snapshots)]
        bus.emit(AppEvent(EventType.RESOLUTION, subject.timestamp_utc,
                          {"synthetic": True, "asset": subject.asset,
                           "winning_side": subject.predicted_side,
                           "classification_correct": (tick // 7) % 4 != 0,
                           "resolution_verification_status": "PROXY_RESOLUTION"}))


# Provider health walks LIVE -> DEGRADED -> LIVE so the degraded treatment and
# its warning routing are both visible without breaking anything.
DEMO_PROVIDER_CYCLE = (
    ("LIVE", ""), ("LIVE", ""), ("LIVE", ""), ("LIVE", ""), ("LIVE", ""),
    ("DEGRADED", "SYNTHETIC PROVIDER DEGRADATION"),
    ("DEGRADED", "SYNTHETIC PROVIDER DEGRADATION"),
    ("LIVE", ""), ("LIVE", ""), ("LIVE", ""),
)


def demo_provider_state(tick: int) -> tuple[str, str]:
    return DEMO_PROVIDER_CYCLE[tick % len(DEMO_PROVIDER_CYCLE)]


DEMO_FORWARD_REPORT = {
    "total_contracts_observed": 96, "total_entry_events": 61, "resolved_entries": 58,
    "abstention_rate": 0.364, "sample_label": "SMALL SAMPLE (SYNTHETIC)",
    "classification_accuracy": 0.9655, "ci_low": 0.9012, "ci_high": 0.9931,
    "yes": {"n": 33, "accuracy": 0.9697}, "no": {"n": 25, "accuracy": 0.96},
    "per_asset": {"BTC-USD": {"n": 14, "accuracy": 1.0}, "ETH-USD": {"n": 12, "accuracy": 0.9167}},
}

DEMO_HISTORICAL = {
    "status": "FORWARD_SAMPLE_TOO_SMALL",
    "historical_accuracy": 0.9658, "historical_coverage": 0.6373,
}
