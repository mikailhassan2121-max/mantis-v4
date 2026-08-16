"""Real measurements about this process, for the left rail's PROCESS module.

Only values that can actually be measured are reported. CPU and resident set
size appear when ``psutil`` happens to be installed and are simply absent
otherwise -- the module renders the rows it has rather than inventing a number
to fill a gap, which is the same rule the rest of the interface follows for
missing market data.
"""

from __future__ import annotations

import os
import platform
import sys
import threading
import time
from typing import Any, Optional

try:                                   # optional, never required
    import psutil                      # type: ignore
except Exception:                      # pragma: no cover - absence is the norm
    psutil = None

_STARTED = time.time()
_PROCESS = None
_PEAK_RSS_MB = 0.0
MEMORY_WARNING_MB = 768.0
MEMORY_CRITICAL_MB = 1200.0
if psutil is not None:
    try:
        _PROCESS = psutil.Process(os.getpid())
        _PROCESS.cpu_percent(None)     # prime the interval sampler
    except Exception:
        _PROCESS = None


def host_payload(clients: Optional[int] = None) -> dict[str, Any]:
    global _PEAK_RSS_MB
    data: dict[str, Any] = {
        "pid": os.getpid(),
        "threads": threading.active_count(),
        "python": platform.python_version(),
        "platform": platform.system(),
        "uptime_seconds": time.time() - _STARTED,
    }
    if clients is not None:
        data["clients"] = clients
    if _PROCESS is not None:
        try:
            data["cpu_percent"] = float(_PROCESS.cpu_percent(None))
            data["rss_mb"] = float(_PROCESS.memory_info().rss) / (1024 * 1024)
            _PEAK_RSS_MB=max(_PEAK_RSS_MB,data["rss_mb"])
            data["rss_peak_mb"]=_PEAK_RSS_MB
            data["memory_status"]=("CRITICAL" if data["rss_mb"]>=MEMORY_CRITICAL_MB else
                                   "WARNING" if data["rss_mb"]>=MEMORY_WARNING_MB else "NORMAL")
        except Exception:
            pass                       # a metric that cannot be read is omitted
    return data
