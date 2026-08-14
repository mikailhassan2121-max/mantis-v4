"""Initialization sequence.

Deliberately fast. It reports the real state of each subsystem as it is handed
in -- nothing here fakes a check or sleeps to look busy. ``startup_animation``
turns the staged reveal off; the same report is then printed in one frame.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable, Optional

from rich.align import Align
from rich.console import Console, Group
from rich.text import Text

from . import theme

LEADER_WIDTH = 58
STEP_DELAY = 0.055          # total sequence stays well under half a second


@dataclass(frozen=True)
class Check:
    label: str
    value: str
    level: str = "ok"       # ok | warn | crit

    @property
    def style(self) -> str:
        return {"ok": "mantis.ok", "warn": "mantis.warn", "crit": "mantis.crit"}[self.level]

    def line(self) -> Text:
        dots = "." * max(3, LEADER_WIDTH - len(self.label) - len(self.value))
        return Text.assemble((self.label + " ", "mantis.label"), (dots, "mantis.unit"),
                             (" " + self.value, self.style))


def build_checks(
    *,
    model_version: str,
    policy_name: str,
    provider_state: str,
    webull_status: str,
    economics_status: str,
    forward_dir: str,
    clock_ok: bool,
    audio_status: str,
    voice_status: str,
    demo_mode: bool = False,
) -> list[Check]:
    def level(value: str, good: Iterable[str]) -> str:
        return "ok" if any(token in value.upper() for token in good) else "warn"

    checks = [
        Check("QUANT ENGINE", "READY"),
        Check("MODEL", model_version),
        Check("ENTRY POLICY", policy_name),
        Check("DATA PROVIDER", provider_state, level(provider_state, ("LIVE", "READY", "OK"))),
        Check("WEBULL", webull_status.replace("_", " "),
              level(webull_status, ("LIVE", "ENABLED"))),
        Check("ECONOMICS ENGINE", economics_status,
              level(economics_status, ("ACTIVE", "AVAILABLE"))),
        Check("FORWARD LOGGER", f"READY  {forward_dir}"),
        Check("CLOCK SYNC", "READY" if clock_ok else "UNAVAILABLE", "ok" if clock_ok else "crit"),
        Check("ALERT AUDIO", audio_status, level(audio_status, ("READY", "WINSOUND", "AVAILABLE"))),
        Check("VOICE", voice_status, level(voice_status, ("READY",))),
    ]
    if demo_mode:
        checks.append(Check("MODE", "DEMO / SYNTHETIC DATA", "warn"))
    return checks


def banner() -> Group:
    return Group(
        Align.center(Text(theme.WORDMARK.strip("\n"), style="mantis.brand")),
        Align.center(Text(theme.PRODUCT_EXPANSION, style="mantis.expansion")),
        Align.center(Text(theme.ADVISORY_BANNER, style="mantis.warn")),
        Text(""),
    )


def run(console: Console, checks: list[Check], animate: bool = True) -> None:
    """Print the sequence. Never raises: a cosmetic failure must not stop boot."""
    try:
        console.print(banner())
        console.print(Text("INITIALIZING...", style="mantis.title"))
        console.print(Text(""))
        for check in checks:
            console.print(check.line())
            if animate:
                time.sleep(STEP_DELAY)
        console.print(Text(""))
        failed = [c for c in checks if c.level == "crit"]
        if failed:
            console.print(Text("SYSTEM DEGRADED — " + ", ".join(c.label for c in failed),
                               style="mantis.crit"))
        else:
            console.print(Text("SYSTEM READY", style="mantis.ok"))
        console.print(Text(""))
        if animate:
            time.sleep(0.2)
    except Exception:
        return


def plain_lines(checks: list[Check]) -> list[str]:
    """Same report without Rich, for --no-ui and for tests."""
    out = [theme.PRODUCT_NAME, theme.PRODUCT_EXPANSION, "", "INITIALIZING...", ""]
    for check in checks:
        dots = "." * max(3, LEADER_WIDTH - len(check.label) - len(check.value))
        out.append(f"{check.label} {dots} {check.value}")
    out.extend(["", "SYSTEM READY" if not any(c.level == "crit" for c in checks)
                else "SYSTEM DEGRADED"])
    return out
