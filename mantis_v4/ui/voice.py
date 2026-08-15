"""Isolated voice-announcement subsystem.

Uses the Windows ``System.Speech`` synthesiser through a short-lived PowerShell
process, the same mechanism V3 used, with the operational problems fixed:

* One daemon worker thread owns synthesis; callers only enqueue.
* The queue is bounded and sheds the oldest pending line, so the scanner is
  never slowed by a synthesiser that is mid-sentence.
* Repeated failures disable the subsystem and surface as a degraded status
  instead of raising into the quant loop.
* Text is restricted to a safe character set before it reaches PowerShell, so
  an asset name from configuration cannot become a command.

Only actionable events are spoken. Metrics are never read aloud.
"""

from __future__ import annotations

import queue
import re
import subprocess
import sys
import threading
from dataclasses import dataclass
from typing import Optional

MAX_PENDING = 4
SPEECH_TIMEOUT_SECONDS = 20
MAX_FAILURES = 3

SAFE_TEXT = re.compile(r"[^A-Za-z0-9 ,.\-%:]")

SPOKEN_ASSET = {
    "BTC-USD": "Bitcoin",
    "ETH-USD": "Ethereum",
    "SOL-USD": "Solana",
    "XRP-USD": "X R P",
    "ADA-USD": "Cardano",
}


def spoken_asset(asset: str) -> str:
    return SPOKEN_ASSET.get(asset, str(asset).replace("-USD", "").replace("-", " "))


def sanitize(text: str) -> str:
    return SAFE_TEXT.sub(" ", str(text)).strip()


@dataclass(frozen=True)
class VoiceStatus:
    available: bool
    backend: str
    detail: str = ""


class VoiceEngine:
    """Asynchronous speech. ``say`` never blocks and never raises."""

    def __init__(self, config, speaker=None):
        self._config = config
        self._queue: queue.Queue = queue.Queue(maxsize=MAX_PENDING)
        self._lock = threading.Lock()
        self._failures = 0
        self._stop = threading.Event()
        self._speaker = speaker or self._speak_windows
        self._available = speaker is not None or sys.platform == "win32"
        self.spoken: list[str] = []          # last lines, for tests/diagnostics
        self._thread = threading.Thread(target=self._run, name="MANTIS-Voice", daemon=True)
        self._thread.start()

    # -- backend ------------------------------------------------------------

    def _speak_windows(self, text: str) -> None:
        command = (
            "Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            f"$s.Rate = {int(self._config.voice_rate)}; "
            f"$s.Volume = {int(self._scaled_volume())}; "
            f"$s.Speak('{text}')"
        )
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            timeout=SPEECH_TIMEOUT_SECONDS,
            check=True,
        )

    def _scaled_volume(self) -> int:
        volume = int(self._config.voice_volume * max(0.0, min(1.0, self._config.master_volume)))
        return max(0, min(100, volume))

    @property
    def status(self) -> VoiceStatus:
        with self._lock:
            if not self._config.voice_enabled:
                return VoiceStatus(False, "system.speech", "DISABLED")
            if not self._available:
                return VoiceStatus(False, "system.speech", "SPEECH SYNTHESIS UNAVAILABLE")
            if self._failures:
                return VoiceStatus(True, "system.speech", f"DEGRADED: {self._failures} failures")
            return VoiceStatus(True, "system.speech", "READY")

    # -- public API ---------------------------------------------------------

    def say(self, text: str) -> bool:
        """Queue one announcement. Returns whether it was accepted."""
        if not self._config.voice_enabled or not self._available:
            return False
        clean = sanitize(text)
        if not clean:
            return False
        try:
            self._queue.put_nowait(clean)
        except queue.Full:
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(clean)
            except (queue.Empty, queue.Full):
                return False
        return True

    def stop(self, timeout: float = 1.0) -> None:
        self._stop.set()
        try:
            self._queue.put_nowait("__stop__")
        except queue.Full:
            pass
        self._thread.join(timeout=timeout)

    def drain(self, timeout: float = 5.0) -> None:
        import time
        end = time.monotonic() + timeout
        while time.monotonic() < end and not self._queue.empty():
            time.sleep(0.01)

    @property
    def is_alive(self) -> bool:
        return self._thread.is_alive()

    # -- worker -------------------------------------------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                text = self._queue.get(timeout=0.25)
            except queue.Empty:
                continue
            if text == "__stop__":
                break
            try:
                self._speaker(text)
                with self._lock:
                    self.spoken.append(text)
                    del self.spoken[:-32]
            except Exception:
                with self._lock:
                    self._failures += 1
                    if self._failures >= MAX_FAILURES:
                        self._available = False


class NullVoiceEngine:
    """Used when voice is disabled. Same shape, guaranteed silence."""

    def __init__(self, *_args, **_kwargs):
        self.spoken: list[str] = []

    @property
    def status(self) -> VoiceStatus:
        return VoiceStatus(False, "null", "DISABLED")

    def say(self, text: str) -> bool:  # noqa: ARG002
        return False

    def stop(self, timeout: float = 0.0) -> None:
        return None

    def drain(self, timeout: float = 0.0) -> None:
        return None

    @property
    def is_alive(self) -> bool:
        return False


def build_voice(config, speaker=None):
    if not config.voice_enabled:
        return NullVoiceEngine()
    return VoiceEngine(config, speaker=speaker)


# ---------------------------------------------------------------------------
# Announcement phrasing
# ---------------------------------------------------------------------------

def phrase_entry(asset: str, side: str) -> str:
    return f"MANTIS. {spoken_asset(asset)}. Enter {side.title()}."

def phrase_primary_selection(asset: str, side: str, ask=None, probability=None) -> str:
    line=f"MANTIS. Primary selection. {spoken_asset(asset)}. Buy {side.title()}."
    if isinstance(ask,(int,float)): line+=f" Contract ask {int(round(ask*100))} cents."
    if isinstance(probability,(int,float)): line+=f" Model probability {probability*100:.1f} percent."
    return line


def phrase_data_hold(asset: str, reason: str = "") -> str:
    if "STALE" in str(reason).upper():
        return f"Data hold. {spoken_asset(asset)} market data stale."
    return f"Data hold. {spoken_asset(asset)}."


def phrase_rollover(asset: Optional[str] = None) -> str:
    if asset:
        return f"New {spoken_asset(asset)} contract window."
    return "New contract window."


def phrase_resolution(asset: str, correct: Optional[bool]) -> str:
    if correct is True:
        return f"{spoken_asset(asset)} contract resolved. Prediction correct."
    if correct is False:
        return f"{spoken_asset(asset)} contract resolved. Prediction incorrect."
    return f"{spoken_asset(asset)} contract resolved. No entry taken."


def phrase_error(component: str) -> str:
    return f"MANTIS system error. {sanitize(component)}."
