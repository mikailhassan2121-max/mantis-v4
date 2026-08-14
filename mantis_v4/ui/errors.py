"""Operator-facing error presentation.

In normal operation a failure is a short, readable block:

    SYSTEM ERROR
    Provider:  yahoo
    Component: market data fetch
    Time:      2026-08-14T18:11:04+00:00
    Recovery:  retrying / degraded mode

The complete traceback is always written to the diagnostics log so nothing is
lost. ``developer_mode`` additionally surfaces it in-band.
"""

from __future__ import annotations

import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .state import SystemError

UTC = timezone.utc


def describe(
    exc: BaseException,
    *,
    provider: str = "n/a",
    component: str = "unknown",
    recovery: str = "retrying / degraded mode",
    when: Optional[datetime] = None,
) -> SystemError:
    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    return SystemError(
        timestamp=when or datetime.now(UTC),
        provider=provider,
        component=component,
        message=f"{type(exc).__name__}: {exc}",
        recovery=recovery,
        traceback_text=text,
    )


def write_diagnostic(error: SystemError, path: Path) -> Optional[Path]:
    """Append the full traceback. Never raises: logging a failure must not fail."""
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        block = (
            f"\n===== {error.timestamp.isoformat()} =====\n"
            f"provider={error.provider} component={error.component}\n"
            f"recovery={error.recovery}\n"
            f"{error.traceback_text or error.message}\n"
        )
        with path.open("a", encoding="utf-8") as handle:
            handle.write(block)
        return path
    except Exception:
        return None


def render_lines(error: SystemError, developer_mode: bool = False) -> list[str]:
    lines = [
        "SYSTEM ERROR",
        f"Provider:  {error.provider}",
        f"Component: {error.component}",
        f"Time:      {error.timestamp.isoformat(timespec='seconds')}",
        f"Recovery:  {error.recovery}",
        f"Detail:    {error.message}",
    ]
    if developer_mode and error.traceback_text:
        lines.append("")
        lines.extend(error.traceback_text.rstrip().splitlines())
    return lines
