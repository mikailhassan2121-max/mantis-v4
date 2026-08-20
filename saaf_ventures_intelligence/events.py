"""Append-only JSONL audit events for SVI orchestration."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
from typing import Any, Mapping, Protocol

UTC = timezone.utc


@dataclass(frozen=True)
class AuditEvent:
    event_id: str
    event_type: str
    timestamp: datetime
    run_id: str
    payload: Mapping[str, Any]
    schema_version: int = 5


class EventSink(Protocol):
    def append(self, event: AuditEvent) -> None: ...


class NullEventSink:
    def append(self, event: AuditEvent) -> None:
        return None


class JsonlEventSink:
    """Process-local serialized append; never rotates or deletes evidence."""
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._event_ids = {str(row.get("event_id")) for row in read_events(self.path)
                           if row.get("event_id") is not None}

    def append(self, event: AuditEvent) -> None:
        record = asdict(event)
        record["timestamp"] = event.timestamp.astimezone(UTC).isoformat()
        line = json.dumps(record, sort_keys=True, default=str, separators=(",", ":")) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            if event.event_id in self._event_ids:
                return
            with self.path.open("a", encoding="utf-8", newline="") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
            self._event_ids.add(event.event_id)


def read_events(path: Path) -> list[dict]:
    """Read valid append-only events, tolerating only a truncated final row."""
    path = Path(path)
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    events = []
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            if line_number == len(lines):
                break
            raise ValueError(f"malformed {path.name} line {line_number}")
        if not isinstance(value, dict) or value.get("schema_version") not in {1, 2, 3, 4, 5}:
            raise ValueError(f"unsupported {path.name} line {line_number}")
        events.append(value)
    return events
