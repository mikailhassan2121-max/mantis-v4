"""Local, read-only HTTP + server-sent-events bridge to the web shell.

Isolation contract, identical in force to the Rich render thread:

* The scan thread only ever **writes** ``CommandCenterState`` under its lock.
* This server only ever **reads** an immutable ``UiSnapshot`` copy.
* Every route is a GET. There is no route that mutates state, and no route that
  touches ``ForwardStore``. The browser is a display, not a controller.
* The socket is bound to the loopback address only.
* The server runs on daemon threads. A wedged, slow or absent browser cannot
  stall a scan, because the scan thread never waits on this module.

Standard library only -- ``http.server`` plus a ``deque`` per client. Adding a
web front end must not add an install step for the operator.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

from . import webmodel

UTC = timezone.utc

WEB_ROOT = Path(__file__).resolve().parent / "web"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
BRANDING_ROOT = next((p for p in (
    PROJECT_ROOT / "assets" / "branding",
    PROJECT_ROOT / "assests" / "branding",  # supplied repository spelling
) if p.is_dir()), PROJECT_ROOT / "assets" / "branding")

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
}

# A slow client is dropped rather than allowed to accumulate frames forever.
MAX_CLIENT_BACKLOG = 8


class _Client:
    """One connected browser. Frames are dropped oldest-first when it lags."""

    def __init__(self) -> None:
        self.frames: list[str] = []
        self.event = threading.Event()
        self.lock = threading.Lock()
        self.closed = False

    def push(self, payload: str) -> None:
        with self.lock:
            self.frames.append(payload)
            if len(self.frames) > MAX_CLIENT_BACKLOG:
                del self.frames[0:len(self.frames) - MAX_CLIENT_BACKLOG]
        self.event.set()

    def drain(self, timeout: float = 1.0) -> list[str]:
        self.event.wait(timeout)
        with self.lock:
            frames, self.frames = self.frames, []
            self.event.clear()
        return frames


class CommandCenterServer:
    """Serves the shell and streams snapshots to it."""

    def __init__(self, state, config, host: Optional[str] = None,
                 port: Optional[int] = None, web_root: Optional[Path] = None):
        self.state = state
        self.config = config
        self.host = host if host is not None else getattr(config, "web_host", "127.0.0.1")
        self.requested_port = port if port is not None else int(getattr(config, "web_port", 0))
        self.web_root = Path(web_root) if web_root else WEB_ROOT
        self._clients: set[_Client] = set()
        self._clients_lock = threading.Lock()
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._serve_thread: Optional[threading.Thread] = None
        self._push_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.failures = 0
        self.disabled_reason: Optional[str] = None

    # -- lifecycle ----------------------------------------------------------

    @property
    def port(self) -> int:
        return self._httpd.server_address[1] if self._httpd else self.requested_port

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    def start(self) -> "CommandCenterServer":
        handler = _make_handler(self)
        self._httpd = ThreadingHTTPServer((self.host, self.requested_port), handler)
        self._httpd.daemon_threads = True
        self._serve_thread = threading.Thread(
            target=self._httpd.serve_forever, name="MANTIS-Web", daemon=True)
        self._serve_thread.start()
        self._push_thread = threading.Thread(
            target=self._push_loop, name="MANTIS-WebPush", daemon=True)
        self._push_thread.start()
        return self

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        with self._clients_lock:
            for client in self._clients:
                client.closed = True
                client.event.set()
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
                self._httpd.server_close()
            except Exception:
                pass
            self._httpd = None
        for thread in (self._push_thread, self._serve_thread):
            if thread is not None:
                thread.join(timeout=timeout)
        self._push_thread = self._serve_thread = None

    # -- streaming ----------------------------------------------------------

    def payload(self) -> dict:
        with self._clients_lock:
            clients = len(self._clients)
        return webmodel.snapshot_payload(self.state.snapshot(), self.config,
                                         datetime.now(UTC), clients)

    def frame(self) -> str:
        return json.dumps(self.payload(), default=str, separators=(",", ":"))

    def register(self) -> _Client:
        client = _Client()
        with self._clients_lock:
            self._clients.add(client)
        return client

    def unregister(self, client: _Client) -> None:
        client.closed = True
        client.event.set()
        with self._clients_lock:
            self._clients.discard(client)

    def _push_loop(self) -> None:
        """Snapshot on the UI cadence and fan out to whoever is listening.

        Every iteration is wrapped: a serialisation failure degrades this
        subsystem and is counted, exactly as a Rich render failure would be.
        It never propagates to the scan thread, which does not call this.
        """
        interval = 1.0 / max(0.2, float(getattr(self.config, "ui_refresh_rate", 4.0)))
        while not self._stop.is_set():
            try:
                with self._clients_lock:
                    listening = bool(self._clients)
                if listening:
                    frame = self.frame()
                    with self._clients_lock:
                        clients = list(self._clients)
                    for client in clients:
                        client.push(frame)
                self.failures = 0
            except Exception as exc:
                self.failures += 1
                if self.failures >= 5 and self.disabled_reason is None:
                    self.disabled_reason = f"{type(exc).__name__}: {exc}"
                    try:
                        self.state.log("WARNING", "UI", "WEB STREAM DEGRADED",
                                       self.disabled_reason)
                    except Exception:
                        pass
            self._stop.wait(interval)

    # -- static assets ------------------------------------------------------

    def read_asset(self, relative: str) -> Optional[tuple[bytes, str]]:
        """Resolve a static file inside the web root, or nothing.

        The resolved path must stay inside the web root: a request may not walk
        out of it with ``..`` or an absolute path.
        """
        target = (self.web_root / relative.lstrip("/")).resolve()
        try:
            target.relative_to(self.web_root.resolve())
        except ValueError:
            return None
        if not target.is_file():
            return None
        content_type = CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream")
        return target.read_bytes(), content_type

    def read_branding(self, relative: str) -> Optional[tuple[bytes, str]]:
        """Read one supplied branding image without exposing the repository."""
        target = (BRANDING_ROOT / relative.lstrip("/")).resolve()
        try:
            target.relative_to(BRANDING_ROOT.resolve())
        except ValueError:
            return None
        if not target.is_file() or target.suffix.lower() != ".png":
            return None
        return target.read_bytes(), "image/png"


def _make_handler(server: CommandCenterServer):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "MANTIS"
        sys_version = ""

        def log_message(self, fmt, *args):
            """Silence the default stderr access log; it would tear the console."""

        def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):                      # noqa: N802  (stdlib naming)
            route = self.path.split("?", 1)[0]
            if route == "/":
                route = "/index.html"

            if route == "/stream":
                self._stream()
                return
            if route == "/snapshot.json":
                self._send(server.frame().encode("utf-8"),
                           "application/json; charset=utf-8")
                return

            if route.startswith("/branding/"):
                asset = server.read_branding(route[len("/branding/"):])
                if asset is None:
                    self._send(b"not found", "text/plain; charset=utf-8", status=404)
                else:
                    self._send(*asset)
                return

            asset = server.read_asset(route)
            if asset is None:
                self._send(b"not found", "text/plain; charset=utf-8", status=404)
                return
            body, content_type = asset
            self._send(body, content_type)

        def _stream(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()

            client = server.register()
            try:
                # Prime the shell with a full frame so it can draw immediately
                # instead of waiting for the next scheduled push.
                self._write_event(server.frame())
                last_beat = time.monotonic()
                while not client.closed and not server._stop.is_set():
                    for frame in client.drain(timeout=1.0):
                        self._write_event(frame)
                    if time.monotonic() - last_beat > 15.0:
                        self.wfile.write(b": beat\n\n")
                        self.wfile.flush()
                        last_beat = time.monotonic()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass                            # the operator closed the window
            finally:
                server.unregister(client)

        def _write_event(self, payload: str) -> None:
            self.wfile.write(b"data: " + payload.encode("utf-8") + b"\n\n")
            self.wfile.flush()

    return Handler
