"""Read-only replay and inspection of SVI orchestration evidence."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .events import read_events


@dataclass(frozen=True)
class ReplaySummary:
    evaluations: int
    candidates: int
    reviewable: int
    blocked: int
    agent_errors: int
    first_timestamp: str | None
    last_timestamp: str | None


class EvidenceReplay:
    def __init__(self, path: Path):
        self.path = Path(path)

    def events(self) -> tuple[dict, ...]:
        rows = read_events(self.path)
        previous = None
        for row in rows:
            stamp = datetime.fromisoformat(row["timestamp"])
            if stamp.tzinfo is None:
                raise ValueError("SVI evidence timestamp must be timezone-aware")
            if previous is not None and stamp < previous:
                raise ValueError("SVI evidence must be chronological")
            previous = stamp
        return tuple(rows)

    def summary(self) -> ReplaySummary:
        rows = [row for row in self.events() if row.get("event_type") == "SUPERVISOR_EVALUATION"]
        payloads = [row.get("payload") or {} for row in rows]
        return ReplaySummary(
            evaluations=len(rows),
            candidates=sum(len(payload.get("candidates") or ()) for payload in payloads),
            reviewable=sum(int(payload.get("reviewable") or 0) for payload in payloads),
            blocked=sum(int(payload.get("blocked") or 0) for payload in payloads),
            agent_errors=sum(len(payload.get("agent_errors") or {}) for payload in payloads),
            first_timestamp=rows[0]["timestamp"] if rows else None,
            last_timestamp=rows[-1]["timestamp"] if rows else None,
        )
