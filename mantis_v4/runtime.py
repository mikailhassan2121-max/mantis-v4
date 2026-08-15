"""Small lifecycle primitives shared by the production runner and tests."""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class WorkerHandle:
    name: str
    stop_event: threading.Event
    thread: threading.Thread

    def stop(self, timeout: float = 3.0) -> bool:
        self.stop_event.set()
        self.thread.join(timeout)
        return not self.thread.is_alive()


def start_worker(name: str, target: Callable[[threading.Event], None], *,
                 daemon: bool = True) -> WorkerHandle:
    stop = threading.Event()
    thread = threading.Thread(target=target, args=(stop,), name=name, daemon=daemon)
    thread.start()
    return WorkerHandle(name, stop, thread)


def stop_browser(process, timeout: float = 2.0) -> bool:
    """Stop only the dedicated app process MANTIS launched; never other browsers."""
    if process is None:
        return True
    try:
        if process.poll() is not None:
            return True
        process.terminate()
        try:
            process.wait(timeout=timeout)
        except Exception:
            process.kill()
            process.wait(timeout=timeout)
        return process.poll() is not None
    except Exception:
        return False


def shutdown_lines(*, forward_safe: bool, server: Optional[bool], reporter: Optional[bool],
                   audio: bool, voice: bool, browser: Optional[bool] = None) -> list[str]:
    def word(value: Optional[bool], stopped: str = "STOPPED") -> str:
        if value is None:
            return "NOT STARTED"
        return stopped if value else "DEGRADED"
    lines = ["MANTIS SHUTDOWN",
             f"FORWARD LOGS .......... {'SAFE' if forward_safe else 'CHECK REQUIRED'}",
             f"SERVER ................ {word(server)}",
             f"REPORTER .............. {word(reporter)}",
             f"AUDIO ................. {word(audio)}",
             f"VOICE ................. {word(voice)}"]
    if browser is not None:
        lines.append(f"BROWSER ............... {word(browser, 'CLOSED')}")
    return lines
