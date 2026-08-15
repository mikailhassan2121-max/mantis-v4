"""Alert severity, routing, and anti-fatigue policy.

The Phase 8 hooks are the only input. This module maps an event onto:

    severity  ->  event-log line  ->  optional tone  ->  optional announcement

It contains no trading logic and writes nothing to the forward store. Handlers
run on the scanner's thread when the engine emits, so every handler is wrapped:
a presentation failure can never propagate into the quant loop.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional
from datetime import datetime, timezone

from ..forward.events import AppEvent, EventBus, EventType
from . import voice as voice_phrases
from .theme import reason_text


class Severity(str, Enum):
    INFO = "INFO"
    NOTICE = "NOTICE"
    ACTION = "ACTION"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class AlertRoute:
    severity: Severity
    audio_key: str            # PresentationConfig audio_<key> switch
    audio_cue: Optional[str]  # audio.CUES name, or None for silence
    voice_key: str            # PresentationConfig voice_<key> switch


# Mapping is declared once, in one place, so the alert policy is auditable.
ROUTES: dict[EventType, AlertRoute] = {
    EventType.ROLLOVER: AlertRoute(Severity.INFO, "rollover", "rollover", "rollover"),
    EventType.RESOLUTION: AlertRoute(Severity.NOTICE, "resolution", "resolution", "resolution"),
    EventType.ENTRY_YES: AlertRoute(Severity.ACTION, "enter_yes", "enter_yes", "enter"),
    EventType.ENTRY_NO: AlertRoute(Severity.ACTION, "enter_no", "enter_no", "enter"),
    EventType.PRIMARY_SELECTION: AlertRoute(Severity.ACTION, "enter_yes", None, "enter"),
    EventType.WAIT: AlertRoute(Severity.INFO, "wait", None, "wait"),
    EventType.DATA_HOLD: AlertRoute(Severity.WARNING, "data_hold", "data_hold", "data_hold"),
    EventType.ERROR: AlertRoute(Severity.CRITICAL, "error", "error", "error"),
}


class AlertRouter:
    """Subscribes to the Phase 8 event bus and drives presentation only."""

    def __init__(self, state, audio, voice, config):
        self.state = state
        self.audio = audio
        self.voice = voice
        self.config = config
        self._lock = threading.Lock()
        self._last_audio: dict[str, float] = {}
        self._last_voice: dict[str, float] = {}
        self._last_wait: dict[str, str] = {}
        self._last_primary_selection: Optional[str] = None
        # The scanner may emit events while the cinematic browser boot is still
        # running. Log them immediately, but do not let operational tones or
        # speech collide with the curated boot soundtrack.
        gate = float(getattr(config, "startup_alert_suppression_seconds", 0.0))
        if bool(getattr(config, "boot_sequence_enabled", False)):
            gate = max(gate, float(getattr(config, "boot_duration", 0.0)) + 1.0)
        self._live_alerts_at = time.monotonic() + gate
        self._armed = gate <= 0.0
        self._online_announced = False
        self.routed: list[tuple[str, str]] = []   # (event type, severity) for tests

    # -- subscription -------------------------------------------------------

    def attach(self, bus: EventBus) -> "AlertRouter":
        for event_type in EventType:
            bus.subscribe(event_type, self._safe_handle)
        return self

    def _safe_handle(self, event: AppEvent) -> None:
        try:
            self.handle(event)
        except Exception:
            # Presentation must never break the scanner. The failure is logged
            # to the in-memory feed and swallowed.
            try:
                self.state.log(Severity.WARNING.value, "ALERTS",
                               "ALERT ROUTING FAILED", str(event.type))
            except Exception:
                pass

    # -- rate limiting ------------------------------------------------------

    def _allow(self, bucket: dict, key: str, minimum: float) -> bool:
        now = time.monotonic()
        with self._lock:
            last = bucket.get(key)
            if last is not None and now - last < minimum:
                return False
            bucket[key] = now
            return True

    # -- routing ------------------------------------------------------------

    def handle(self, event: AppEvent) -> Optional[Severity]:
        route = ROUTES.get(event.type)
        if route is None:
            return None
        payload = event.payload or {}
        asset = str(payload.get("asset") or "")

        if event.type in (EventType.ENTRY_YES, EventType.ENTRY_NO, EventType.PRIMARY_SELECTION):
            self._bump("voice_events_received")
        if event.type in (EventType.ENTRY_YES, EventType.ENTRY_NO):
            # Phase-8 per-asset entries are research records, never operator commands.
            self._bump("voice_events_suppressed")

        if event.type is EventType.PRIMARY_SELECTION:
            if not self._actionable_primary(payload):
                self._bump("voice_events_suppressed")
                if getattr(self.config,"operator_diagnostics",False):
                    print("MANTIS_OPERATOR VOICE_SUPPRESSED source=AlertRouter event=ON_PRIMARY_SELECTION")
                self.state.log(Severity.WARNING.value, asset or "SYSTEM",
                    "PRIMARY VOICE SUPPRESSED", "authoritative persisted selection validation failed")
                return None
            identity=f"{payload.get('contract_id','')}|{asset}|{payload.get('side','')}"
            with self._lock:
                if self._last_primary_selection==identity: return None
                self._last_primary_selection=identity

        if event.type is EventType.WAIT and not self._wait_changed(asset, payload):
            return None   # identical WAIT reason: keep the log readable

        message, detail, spoken = self._describe(event, payload, asset)
        self.state.log(route.severity.value, asset or "SYSTEM", message, detail)
        self.routed.append((event.type.value, route.severity.value))
        del self.routed[:-64]

        live_alerts = self._armed or time.monotonic() >= self._live_alerts_at
        audio_cue=route.audio_cue
        audio_key=route.audio_key
        if event.type is EventType.PRIMARY_SELECTION:
            audio_cue="enter_no" if str(payload.get("side")).upper()=="NO" else "enter_yes"
            audio_key=audio_cue
        if live_alerts and audio_cue and self.config.audio_allows(audio_key):
            if self._allow(self._last_audio, f"{audio_cue}:{asset}",
                           self.config.audio_min_interval_seconds):
                self.audio.play(audio_cue)

        if live_alerts and spoken and self.config.voice_allows(route.voice_key):
            if self._allow(self._last_voice, f"{route.voice_key}:{asset}",
                           self.config.voice_min_interval_seconds):
                self.voice.say(spoken)
                if event.type is EventType.PRIMARY_SELECTION:
                    self._bump("actionable_voice_events")
                    if getattr(self.config,"operator_diagnostics",False):
                        print("MANTIS_OPERATOR VOICE_ENQUEUE source=AlertRouter event=ON_PRIMARY_SELECTION")

        return route.severity

    def _bump(self, name: str) -> None:
        try:
            status=self.state.snapshot().status
            self.state.set_status(**{name:int(getattr(status,name,0))+1})
        except Exception:
            pass

    def _actionable_primary(self, payload: dict) -> bool:
        """Final voice security boundary; upstream events are never trusted."""
        try:
            op=self.state.operator_state() or {}
            current=op.get("primary_selection") or {}
            if op.get("selection_policy_version") != "PRIMARY_SELECTOR_V1" or not op.get("persisted"):
                return False
            if current.get("asset") not in {"BTC-USD","ETH-USD","SOL-USD","XRP-USD"}:
                return False
            if current.get("side") not in {"YES","NO"}:
                return False
            if not current.get("economically_valid") or not current.get("quote_verified"):
                return False
            if current.get("fee_provenance") != "WEBULL_OFFICIAL_FEE_SCHEDULE":
                return False
            if float(current.get("net_ev") or 0) <= 0 or float(current.get("conservative_net_ev") or 0) <= 0:
                return False
            if any(str(current.get(k) or "") != str(payload.get(k) or "") for k in ("asset","side","contract_id")):
                return False
            end=op.get("window_end_utc")
            if end:
                parsed=datetime.fromisoformat(str(end).replace("Z","+00:00"))
                if parsed <= datetime.now(timezone.utc): return False
            return True
        except Exception:
            return False

    def _wait_changed(self, asset: str, payload: dict) -> bool:
        reason = str(payload.get("reason") or "")
        with self._lock:
            if self._last_wait.get(asset) == reason:
                return False
            self._last_wait[asset] = reason
            return True

    def _describe(self, event: AppEvent, payload: dict, asset: str):
        """Return (log message, log detail, spoken line or None)."""
        if event.type in (EventType.ENTRY_YES, EventType.ENTRY_NO):
            side = str(payload.get("side") or ("YES" if event.type is EventType.ENTRY_YES else "NO"))
            seconds = payload.get("seconds_remaining")
            detail = f"T-{int(seconds):d}s" if isinstance(seconds, (int, float)) else ""
            probability = payload.get("model_probability")
            if isinstance(probability, (int, float)):
                detail = f"{detail} p={probability:.1%}".strip()
            return f"QUALIFIED {side}", detail, None

        if event.type is EventType.PRIMARY_SELECTION:
            side=str(payload.get("side") or "")
            probability=payload.get("confidence"); ask=payload.get("ask")
            detail=(f"ASK ${ask:.2f}  P {probability:.1%}" if isinstance(ask,(int,float)) and isinstance(probability,(int,float)) else "")
            return f"PRIMARY SELECTION — BUY {side}", detail, voice_phrases.phrase_primary_selection(asset,side,ask,probability)

        if event.type is EventType.DATA_HOLD:
            reason = str(payload.get("reason") or "")
            return "DATA HOLD", reason_text(reason), voice_phrases.phrase_data_hold(asset, reason)

        if event.type is EventType.WAIT:
            reason = str(payload.get("reason") or "")
            return "WAIT", reason_text(reason), None   # WAIT is silent by design

        if event.type is EventType.ROLLOVER:
            window = str(payload.get("to") or "")
            return "CONTRACT ROLLOVER", window, voice_phrases.phrase_rollover(asset or None)

        if event.type is EventType.RESOLUTION:
            correct = payload.get("classification_correct")
            winner = str(payload.get("winning_side") or "")
            verdict = "CORRECT" if correct is True else "INCORRECT" if correct is False else "NO ENTRY"
            return (f"RESOLVED {winner}", f"{payload.get('resolution_verification_status', '')} {verdict}".strip(),
                    voice_phrases.phrase_resolution(asset, correct))

        component = str(payload.get("component") or "SYSTEM")
        message = str(payload.get("message") or "")
        return f"SYSTEM ERROR: {component}", message, voice_phrases.phrase_error(component)

    # -- direct (non-hook) notices -----------------------------------------

    def arm(self, announce: bool = True) -> bool:
        """Arm operational alerts at presentation READY, exactly once."""
        with self._lock:
            first = not self._armed
            self._armed = True
            should_announce = announce and not self._online_announced
            if should_announce:
                self._online_announced = True
        if should_announce and self.config.voice_enabled:
            try:
                self.voice.say("MANTIS online.")
            except Exception:
                pass
        return first

    def provider_failure(self, provider: str, detail: str) -> None:
        """Provider health degradation. Routed as a warning, not a hook event."""
        self.state.log(Severity.WARNING.value, "PROVIDER",
                       f"PROVIDER DEGRADED: {provider}", detail)
        if (self._armed or time.monotonic() >= self._live_alerts_at) and self.config.audio_allows("error") and self._allow(
                self._last_audio, f"provider:{provider}", self.config.audio_min_interval_seconds):
            self.audio.play("provider_failure")

    def notice(self, severity: Severity, source: str, message: str, detail: str = "") -> None:
        self.state.log(severity.value, source, message, detail)


# ---------------------------------------------------------------------------
# Test-alert mode
# ---------------------------------------------------------------------------

TEST_SEQUENCE = (
    ("ENTER YES", "enter_yes", lambda: voice_phrases.phrase_entry("BTC-USD", "YES")),
    ("ENTER NO", "enter_no", lambda: voice_phrases.phrase_entry("ETH-USD", "NO")),
    ("DATA HOLD", "data_hold", lambda: voice_phrases.phrase_data_hold("SOL-USD", "STALE_DATA")),
    ("CONTRACT ROLLOVER", "rollover", lambda: voice_phrases.phrase_rollover("XRP-USD")),
    ("RESOLUTION", "resolution", lambda: voice_phrases.phrase_resolution("ADA-USD", True)),
    ("SYSTEM ERROR", "error", lambda: voice_phrases.phrase_error("MARKET DATA PROVIDER")),
)


def run_test_alerts(audio, voice, config, pause: float = 1.2, emit=print) -> list[str]:
    """Preview every alert without producing any signal.

    Nothing here constructs a ``LiveAssetState``, touches ``ForwardEngine`` or
    opens ``ForwardStore``: test alerts cannot reach the forward-validation
    logs, because this function has no path to them.
    """
    played: list[str] = []
    emit("MANTIS ALERT TEST — NO SIGNALS PRODUCED, NOTHING WRITTEN TO FORWARD LOGS")
    for label, cue, phrase in TEST_SEQUENCE:
        line = phrase()
        emit(f"  {label:<20}  tone={cue:<16} voice=\"{line}\"")
        audio.play(cue)
        voice.say(line)
        played.append(cue)
        time.sleep(max(0.0, pause))
    audio.drain()
    voice.drain()
    emit("ALERT TEST COMPLETE")
    return played
