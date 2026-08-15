"""Phase 9 presentation layer: MANTIS Command Center.

Strict separation of concerns. Nothing in this package computes a probability,
an expected value, an edge, a threshold or a contract boundary. It consumes
backend state through ``LiveSnapshot`` and the Phase 8 event hooks, and it
produces pixels, tones and speech.

Layout of the package:

    settings.py   presentation configuration (file + env + CLI)
    theme.py      palette, glyphs, decision-state descriptors, branding
    state.py      thread-safe read model shared with the quant loop
    panels.py     Rich renderables
    dashboard.py  layout assembly and the isolated render thread
    startup.py    initialization sequence
    alerts.py     severity levels and hook routing
    audio.py      non-blocking tone subsystem
    voice.py      non-blocking speech subsystem
    errors.py     operator-facing error presentation
    demo.py       synthetic states, structurally unable to reach forward logs
"""

from .alerts import AlertRouter, Severity, run_test_alerts
from .audio import build_audio
from .dashboard import CommandCenter, build_console, render_to_text
from .settings import PresentationConfig
from .state import AssetView, CommandCenterState, SystemStatus, UiSnapshot, view_from_snapshot
from .voice import build_voice
from .webmodel import snapshot_payload
from .webserver import CommandCenterServer

__all__ = [
    "AlertRouter", "AssetView", "CommandCenter", "CommandCenterServer",
    "CommandCenterState", "PresentationConfig", "Severity", "SystemStatus",
    "UiSnapshot", "build_audio", "build_console", "build_voice",
    "render_to_text", "run_test_alerts", "snapshot_payload", "view_from_snapshot",
]
