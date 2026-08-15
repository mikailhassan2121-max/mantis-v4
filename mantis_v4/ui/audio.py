"""Isolated alert-tone subsystem.

Design constraint: audio must never delay or fail the quantitative loop.

* Playback happens on one daemon worker thread.
* The queue is bounded and drops the *oldest* pending cue when full, so a
  wedged audio device can never apply back-pressure to the scanner.
* Every device call is wrapped; a failing device degrades to the terminal bell
  and then to silence, and the subsystem reports itself as unavailable.

Windows ``winsound.Beep`` has no amplitude control, so ``master_volume``
modulates tone duration and gates the quietest cues rather than pretending to
set a level.
"""

from __future__ import annotations

import queue
import sys
import threading
import time
from dataclasses import dataclass
from typing import Optional, Sequence

# (frequency Hz, duration ms). Restrained, short, and distinguishable by shape
# rather than by loudness.
Tone = tuple[int, int]

CUES: dict[str, tuple[Tone, ...]] = {
    "enter_yes": ((784, 90), (1047, 130)),        # ascending: action, positive
    "enter_no": ((784, 90), (523, 130)),          # descending: action, distinct
    "data_hold": ((330, 120), (330, 120)),        # flat low double: technical
    "rollover": ((587, 70),),                     # single soft mid: informational
    "resolution": ((659, 70), (784, 110)),        # short two-step: notice
    "provider_failure": ((440, 110), (349, 160)), # falling pair: warning
    "error": ((262, 130), (262, 130), (262, 200)),  # low triple: critical
}

MAX_PENDING = 8


@dataclass(frozen=True)
class AudioStatus:
    available: bool
    backend: str
    detail: str = ""


class AudioEngine:
    """Fire-and-forget tone player. ``play`` never blocks and never raises."""

    def __init__(self, config, backend=None):
        self._config = config
        self._queue: queue.Queue = queue.Queue(maxsize=MAX_PENDING)
        self._lock = threading.Lock()
        self._failures = 0
        self._stop = threading.Event()
        self._backend, self._backend_name = self._resolve_backend(backend)
        self._available = self._backend is not None
        self.played: list[str] = []          # last cue names, for tests/diagnostics
        self._thread = threading.Thread(target=self._run, name="MANTIS-Audio", daemon=True)
        self._thread.start()

    # -- backend ------------------------------------------------------------

    def _resolve_backend(self, backend):
        if backend is not None:
            return backend, "injected"
        if sys.platform == "win32":
            try:
                import winsound
                return winsound.Beep, "winsound"
            except Exception:
                pass
        return self._bell, "terminal-bell"

    @staticmethod
    def _bell(frequency: int, duration: int) -> None:  # noqa: ARG004 - shape parity
        sys.stdout.write("\a")
        sys.stdout.flush()

    @property
    def status(self) -> AudioStatus:
        with self._lock:
            if not self._config.audio_enabled:
                return AudioStatus(False, self._backend_name, "DISABLED")
            if not self._available:
                return AudioStatus(False, self._backend_name, "DEVICE UNAVAILABLE")
            return AudioStatus(True, self._backend_name, f"{self._failures} failures")

    # -- public API ---------------------------------------------------------

    def play(self, cue: str) -> bool:
        """Queue a cue. Returns whether it was accepted (never raises)."""
        if not self._config.audio_enabled or not self._available:
            return False
        if self._config.master_volume <= 0.0:
            return False
        tones = CUES.get(cue)
        if not tones:
            return False
        try:
            self._queue.put_nowait(cue)
        except queue.Full:
            # Drop the oldest pending cue rather than the newest event or the
            # calling thread's time.
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(cue)
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

    def drain(self, timeout: float = 2.0) -> None:
        """Test/utility helper: wait for queued cues to finish playing."""
        end = time.monotonic() + timeout
        while time.monotonic() < end and not self._queue.empty():
            time.sleep(0.01)

    @property
    def is_alive(self) -> bool:
        return self._thread.is_alive()

    # -- worker -------------------------------------------------------------

    def _scaled(self, tones: Sequence[Tone]) -> list[Tone]:
        volume = max(0.0, min(1.0, float(self._config.master_volume)))
        return [(f, max(30, int(d * (0.4 + 0.6 * volume)))) for f, d in tones]

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                cue = self._queue.get(timeout=0.25)
            except queue.Empty:
                continue
            if cue == "__stop__":
                break
            tones = CUES.get(cue)
            if not tones:
                continue
            try:
                for frequency, duration in self._scaled(tones):
                    if self._stop.is_set():
                        break
                    self._backend(frequency, duration)
                with self._lock:
                    self.played.append(cue)
                    del self.played[:-32]
            except Exception:
                with self._lock:
                    self._failures += 1
                    if self._failures >= 3:
                        self._available = False


class NullAudioEngine:
    """Used when audio is disabled. Same shape, guaranteed silence."""

    def __init__(self, *_args, **_kwargs):
        self.played: list[str] = []

    @property
    def status(self) -> AudioStatus:
        return AudioStatus(False, "null", "DISABLED")

    def play(self, cue: str) -> bool:  # noqa: ARG002
        return False

    def stop(self, timeout: float = 0.0) -> None:
        return None

    def drain(self, timeout: float = 0.0) -> None:
        return None

    @property
    def is_alive(self) -> bool:
        return False


def build_audio(config, backend=None):
    if not config.audio_enabled:
        return NullAudioEngine()
    return AudioEngine(config, backend=backend)
