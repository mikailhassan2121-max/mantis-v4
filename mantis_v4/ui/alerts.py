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
        # The scanner may emit events while the cinematic browser boot is still
        # running. Log them immediately, but do not let operational tones or
        # speech collide with the curated boot soundtrack.
        gate = float(getattr(config, "startup_alert_suppression_seconds", 0.0))
        if bool(getattr(config, "boot_sequence_enabled", False)):
            gate = max(gate, float(getattr(config, "boot_duration", 0.0)) + 1.0)
        self._live_alerts_at = time.monotonic() + gate
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

        if event.type is EventType.WAIT and not self._wait_changed(asset, payload):
            return None   # identical WAIT reason: keep the log readable

        message, detail, spoken = self._describe(event, payload, asset)
        self.state.log(route.severity.value, asset or "SYSTEM", message, detail)
        self.routed.append((event.type.value, route.severity.value))
        del self.routed[:-64]

        live_alerts = time.monotonic() >= self._live_alerts_at
        if live_alerts and route.audio_cue and self.config.audio_allows(route.audio_key):
            if self._allow(self._last_audio, f"{route.audio_cue}:{asset}",
                           self.config.audio_min_interval_seconds):
                self.audio.play(route.audio_cue)

        if live_alerts and spoken and self.config.voice_allows(route.voice_key):
            if self._allow(self._last_voice, f"{route.voice_key}:{asset}",
                           self.config.voice_min_interval_seconds):
                self.voice.say(spoken)

        return route.severity

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
            return f"ENTER {side}", detail, voice_phrases.phrase_entry(asset, side)

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

    def provider_failure(self, provider: str, detail: str) -> None:
        """Provider health degradation. Routed as a warning, not a hook event."""
        self.state.log(Severity.WARNING.value, "PROVIDER",
                       f"PROVIDER DEGRADED: {provider}", detail)
        if self.config.audio_allows("error") and self._allow(
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
