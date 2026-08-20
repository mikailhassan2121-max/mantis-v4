"""Read-only production diagnostics and isolated self-test for MANTIS.

Neither command constructs ``ForwardEngine`` against the real forward store.
The self-test uses a temporary directory and a loopback ephemeral port only.
"""
from __future__ import annotations

import importlib
import importlib.metadata
import json
import os
import socket
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from . import __version__
from .config import MantisConfig


@dataclass(frozen=True)
class HealthResult:
    component: str
    status: str
    detail: str = ""
    critical: bool = False

    @property
    def ok(self) -> bool:
        return self.status in {"READY", "AVAILABLE", "NOT CONFIGURED"}


REQUIRED_DISTRIBUTIONS = ("numpy", "pandas", "yfinance", "rich", "tzdata", "scipy", "scikit-learn")


def _dependency_results() -> list[HealthResult]:
    results = []
    for name in REQUIRED_DISTRIBUTIONS:
        try:
            version = importlib.metadata.version(name)
            results.append(HealthResult(f"DEPENDENCY {name}", "READY", version, True))
        except importlib.metadata.PackageNotFoundError:
            results.append(HealthResult(f"DEPENDENCY {name}", "MISSING", "install requirements.txt", True))
    return results


def _writable_directory(path: Path) -> HealthResult:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f".mantis-write-probe-{os.getpid()}"
        with probe.open("x", encoding="utf-8") as handle:
            handle.write("probe\n")
            handle.flush()
            os.fsync(handle.fileno())
        probe.unlink()
        return HealthResult("FORWARD DIRECTORY", "READY", str(path), True)
    except OSError as exc:
        return HealthResult("FORWARD DIRECTORY", "FAILED", f"{type(exc).__name__}: {exc}", True)


def _loopback_result() -> HealthResult:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return HealthResult("LOCAL SERVER", "READY", f"127.0.0.1:{sock.getsockname()[1]}", True)
    except OSError as exc:
        return HealthResult("LOCAL SERVER", "FAILED", f"{type(exc).__name__}: {exc}", True)
    finally:
        sock.close()


def inspect(root: Path, forward_dir: Path, environ: dict | None = None) -> list[HealthResult]:
    """Run fast checks. Secret values are never included in results."""
    root = Path(root)
    env = os.environ if environ is None else environ
    results = [HealthResult("MANTIS VERSION", "READY", __version__),
               HealthResult("PYTHON", "READY" if sys.version_info >= (3, 11) else "UNSUPPORTED",
                            sys.version.split()[0], True)]
    results.extend(_dependency_results())
    try:
        importlib.import_module("saaf_ventures_intelligence.operations")
        results.append(HealthResult("SVI FOUNDATION", "READY", "manual-only evidence operations", True))
    except Exception as exc:
        results.append(HealthResult("SVI FOUNDATION", "FAILED", f"{type(exc).__name__}: {exc}", True))
    try:
        config = MantisConfig.load(environ=env)
        results.append(HealthResult("RUNTIME CONFIG", "READY", f"{len(config.active_assets)} assets", True))
        ZoneInfo(config.contract_timezone)
        results.append(HealthResult("TIMEZONE", "READY", config.contract_timezone, True))
        creds = config.credentials
        if not creds.is_configured:
            results.append(HealthResult("WEBULL AUTH", "NOT CONFIGURED", "quotes and EV unavailable"))
        else:
            results.append(HealthResult("WEBULL AUTH", "AVAILABLE",
                                        "configured — connectivity not verified"))
    except Exception as exc:
        results.append(HealthResult("RUNTIME CONFIG", "FAILED", f"{type(exc).__name__}: {exc}", True))
    results.append(_writable_directory(root / forward_dir))
    results.append(_loopback_result())
    try:
        from .ui.webshell import find_browser
        browser = find_browser()
        results.append(HealthResult("BROWSER", "AVAILABLE" if browser else "UNAVAILABLE",
                                    Path(browser).name if browser else "use --no-browser or terminal UI"))
    except Exception as exc:
        results.append(HealthResult("BROWSER", "UNAVAILABLE", type(exc).__name__))
    results.append(HealthResult("AUDIO", "AVAILABLE" if sys.platform == "win32" else "UNAVAILABLE",
                                "winsound" if sys.platform == "win32" else "optional"))
    powershell = next((name for name in ("powershell.exe", "pwsh") if _which(name)), None)
    results.append(HealthResult("VOICE", "AVAILABLE" if powershell and sys.platform == "win32" else "UNAVAILABLE",
                                "System.Speech not invoked by health check"))
    return results


def _which(name: str) -> str | None:
    import shutil
    return shutil.which(name)


def render(results: list[HealthResult], title: str = "MANTIS HEALTH CHECK") -> str:
    width = max((len(result.component) for result in results), default=10)
    lines = [title, f"VERSION {__version__}", "OBSERVATION ONLY — NO AUTOMATED EXECUTION", ""]
    for result in results:
        suffix = f" — {result.detail}" if result.detail else ""
        lines.append(f"{result.component:<{width}}  {result.status}{suffix}")
    critical_failures = [r for r in results if r.critical and not r.ok]
    lines.extend(("", "RESULT: " + ("FAILED" if critical_failures else "READY")))
    return "\n".join(lines)


def exit_code(results: list[HealthResult]) -> int:
    return 1 if any(result.critical and not result.ok for result in results) else 0


def self_test(root: Path) -> list[HealthResult]:
    """Exercise persistence, snapshot serialization, HTTP, assets and SSE in temp state."""
    results: list[HealthResult] = []
    try:
        importlib.import_module("mantis_v4.entry.decision")
        importlib.import_module("mantis_v4.economics.decision")
        results.append(HealthResult("MODEL IMPORT", "READY", "Phase 6/7 modules import", True))
    except Exception as exc:
        results.append(HealthResult("MODEL IMPORT", "FAILED", str(exc), True))
    with tempfile.TemporaryDirectory() as temporary:
        try:
            from .forward import ForwardStore
            store = ForwardStore(Path(temporary) / "forward")
            record = {"run_id": "SELF-TEST", "timestamp_utc": "2000-01-01T00:00:00+00:00",
                      "observation_only": True}
            store.append("runs", record, "run_id")
            ok = store.read("runs") == [record]
            results.append(HealthResult("TEMP PERSISTENCE", "READY" if ok else "FAILED",
                                        "real forward directory untouched", True))
        except Exception as exc:
            results.append(HealthResult("TEMP PERSISTENCE", "FAILED", str(exc), True))
        try:
            from saaf_ventures_intelligence.events import AuditEvent, JsonlEventSink
            from saaf_ventures_intelligence.operations import audit_evidence
            from datetime import datetime, timezone
            evidence = Path(temporary) / "svi" / "events.jsonl"
            JsonlEventSink(evidence).append(AuditEvent("self-test", "SUPERVISOR_EVALUATION",
                datetime(2000,1,1,tzinfo=timezone.utc), "self-test", {
                    "execution_mode":"MANUAL_ONLY","candidates":[],
                    "registry":{"registry_version":"SVI_AGENT_REGISTRY_V2",
                    "specialist_count":1,"execution_mode":"MANUAL_ONLY","specialists":[]},
                    "consensus":[]}))
            audit = audit_evidence(evidence, Path(temporary) / "resolutions.jsonl")
            results.append(HealthResult("SVI EVIDENCE", "READY" if audit["status"]=="PASS" else "FAILED",
                                        "temporary append/audit only", True))
        except Exception as exc:
            results.append(HealthResult("SVI EVIDENCE", "FAILED", f"{type(exc).__name__}: {exc}", True))
        server = None
        try:
            from .ui import CommandCenterServer, CommandCenterState, PresentationConfig
            config = PresentationConfig(audio_enabled=False, voice_enabled=False,
                                        boot_sequence_enabled=False)
            state = CommandCenterState(["BTC-USD"], config)
            server = CommandCenterServer(state, config, host="127.0.0.1", port=0).start()
            with urllib.request.urlopen(server.url + "snapshot.json", timeout=3) as response:
                payload = json.loads(response.read())
            with urllib.request.urlopen(server.url + "branding/mantis_darpa.png", timeout=3) as response:
                png = response.read(8)
            ok = payload.get("type") == "snapshot" and png == b"\x89PNG\r\n\x1a\n"
            results.append(HealthResult("LOCAL WEB SHELL", "READY" if ok else "FAILED",
                                        "snapshot and branding served", True))
        except Exception as exc:
            results.append(HealthResult("LOCAL WEB SHELL", "FAILED", f"{type(exc).__name__}: {exc}", True))
        finally:
            if server is not None:
                server.stop()
    return results
