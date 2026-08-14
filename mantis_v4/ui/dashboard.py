"""Command-center layout assembly and the render loop.

Concurrency contract
--------------------

The quantitative loop owns the main thread and only ever *writes* to
``CommandCenterState`` under its lock. This module owns one daemon render
thread that only ever *reads* an immutable ``UiSnapshot``. There is no shared
mutable object between them and no callback from the renderer into the engine,
so a slow or failing render cannot stall a scan.

Every render is wrapped. After ``MAX_RENDER_FAILURES`` consecutive failures the
interface disables itself, reports the reason, and the scanner keeps running
with the plain console renderer.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from rich.console import Console
from rich.layout import Layout
from rich.live import Live

from . import panels
from .state import CommandCenterState, UiSnapshot
from .theme import MANTIS_THEME

UTC = timezone.utc

MAX_RENDER_FAILURES = 5


def build_console(config, **kwargs) -> Console:
    return Console(theme=MANTIS_THEME, highlight=False, soft_wrap=False, **kwargs)


class CommandCenter:
    """Builds the layout and, optionally, drives a live full-screen render."""

    def __init__(self, state: CommandCenterState, config, console: Optional[Console] = None,
                 local_timezone: str = "America/New_York",
                 ascii_only: Optional[bool] = None):
        self.state = state
        self.config = config
        self.console = console or build_console(config)
        self._ascii_override = ascii_only
        try:
            self.local_tz = ZoneInfo(local_timezone)
        except Exception:
            self.local_tz = UTC
        self.render_failures = 0
        self.render_count = 0
        self.disabled_reason: Optional[str] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # -- geometry -----------------------------------------------------------

    @property
    def ascii_only(self) -> bool:
        """Fall back to ASCII on legacy consoles and on non-Unicode code pages.

        A cp1252 console cannot encode the block glyphs; drawing them anyway
        would raise mid-frame, so the whole visual language degrades together.
        """
        if self._ascii_override is not None:
            return self._ascii_override
        if getattr(self.console, "legacy_windows", False):
            return True
        encoding = str(getattr(self.console, "encoding", "") or "").lower()
        return not encoding.startswith("utf")

    def fits(self) -> bool:
        width, height = self.console.size
        return (width >= self.config.minimum_terminal_width
                and height >= self.config.minimum_terminal_height)

    # -- layout -------------------------------------------------------------

    def build(self, snapshot: Optional[UiSnapshot] = None,
              now: Optional[datetime] = None) -> object:
        """Assemble the whole screen. Pure: safe to call from a test."""
        snapshot = snapshot if snapshot is not None else self.state.snapshot()
        now = now or datetime.now(UTC)
        ascii_only = self.ascii_only

        width, height = self.console.size
        if not self.fits():
            return panels.too_small_panel(width, height,
                                          self.config.minimum_terminal_width,
                                          self.config.minimum_terminal_height)

        focus = next((v for v in snapshot.assets if v.asset == snapshot.focus), None)
        if focus is None and snapshot.assets:
            focus = snapshot.assets[0]

        # Fixed-height bands are sized from the real terminal so nothing is
        # ever drawn into a region smaller than its content.
        cards = height >= 44 and width >= 150
        assets_size = 15 if cards else 3 + len(snapshot.assets)
        bottom_min = 6
        top_size = 15 if cards else 14
        # All five assets always stay visible; the decision band gives way first.
        spare = height - 6 - assets_size - bottom_min
        if spare < top_size:
            top_size = max(10, spare)

        root = Layout(name="root")
        root.split_column(
            Layout(name="header", size=6),
            Layout(name="top", size=top_size),
            Layout(name="assets", size=assets_size),
            Layout(name="bottom", ratio=1, minimum_size=bottom_min),
        )
        # Below ~116 columns three top panels cannot all keep their minimum
        # width, so provider status moves into the bottom auxiliary slot rather
        # than being clipped at the screen edge.
        three_up = width >= 116
        top_parts = [Layout(name="decision", ratio=5, minimum_size=42),
                     Layout(name="economics", ratio=2, minimum_size=28)]
        if three_up:
            top_parts.append(Layout(name="provider", ratio=2, minimum_size=26))
        root["top"].split_row(*top_parts)
        root["bottom"].split_row(
            Layout(name="log", ratio=3, minimum_size=36),
            Layout(name="aux", ratio=2, minimum_size=30),
        )

        local_now = now.astimezone(self.local_tz)
        root["header"].update(panels.header_panel(snapshot, now, local_now, ascii_only))
        root["decision"].update(panels.decision_panel(focus, now, ascii_only, compact=not cards))
        root["economics"].update(panels.economics_panel(focus, ascii_only))
        if three_up:
            root["provider"].update(panels.provider_panel(snapshot.status, ascii_only))
        root["assets"].update(panels.assets_panel(snapshot, now, ascii_only, compact=not cards))
        root["log"].update(panels.event_log_panel(
            snapshot.events, self.config.event_log_visible_rows, self.local_tz, ascii_only))

        if snapshot.last_error is not None:
            root["aux"].update(panels.error_panel(snapshot.last_error, self.config.developer_mode))
        elif not three_up:
            root["aux"].update(panels.provider_panel(snapshot.status, ascii_only))
        elif self.config.show_advanced_diagnostics:
            root["aux"].update(panels.diagnostics_panel(focus, snapshot.status, ascii_only))
        elif self.config.show_forward_validation:
            root["aux"].update(
                panels.forward_panel(snapshot.forward, snapshot.historical, ascii_only))
        else:
            root["aux"].update(panels.decisions_panel(snapshot, now, ascii_only))
        return root

    def render_once(self, live: Optional[Live] = None) -> bool:
        """One protected render. Returns False when the UI has disabled itself."""
        if self.disabled_reason:
            return False
        try:
            frame = self.build()
            if live is not None:
                live.update(frame, refresh=True)
            else:
                self.console.print(frame)
            self.render_count += 1
            self.render_failures = 0
            return True
        except Exception as exc:
            self.render_failures += 1
            if self.render_failures >= MAX_RENDER_FAILURES:
                self.disabled_reason = f"{type(exc).__name__}: {exc}"
                try:
                    self.state.log("WARNING", "UI", "INTERFACE DISABLED",
                                   self.disabled_reason)
                except Exception:
                    pass
                return False
            return True

    # -- render thread ------------------------------------------------------

    def start(self) -> "CommandCenter":
        if self._thread is not None:
            return self
        self._thread = threading.Thread(target=self._run, name="MANTIS-Render", daemon=True)
        self._thread.start()
        return self

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def _run(self) -> None:
        interval = self.config.refresh_interval
        try:
            with Live(console=self.console, screen=True, auto_refresh=False,
                      redirect_stdout=False, redirect_stderr=False,
                      transient=False) as live:
                while not self._stop.is_set():
                    if not self.render_once(live):
                        break
                    self._stop.wait(interval)
        except Exception as exc:
            self.disabled_reason = f"{type(exc).__name__}: {exc}"
            try:
                self.state.log("WARNING", "UI", "INTERFACE UNAVAILABLE", self.disabled_reason)
            except Exception:
                pass


def render_to_text(state: CommandCenterState, config, width: int = 200,
                   height: int = 50, now: Optional[datetime] = None,
                   ascii_only: bool = False, color: bool = True) -> str:
    """Render one frame into a string. Used by tests and textual mockups."""
    import io
    console = build_console(config, file=io.StringIO(), width=width, height=height,
                            force_terminal=color, legacy_windows=False,
                            color_system="truecolor" if color else None)
    center = CommandCenter(state, config, console=console, ascii_only=ascii_only)
    console.print(center.build(now=now))
    return console.file.getvalue()
