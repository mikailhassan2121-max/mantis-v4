"""Open the command center in a Chromium app window.

``--app=`` gives a window with no tab strip, no address bar and no browser
chrome, which is what makes the shell read as an application rather than a web
page. Edge ships with Windows 10/11, so on the target machine this needs no
install; Chrome and Brave are accepted too.

Launching is best-effort by design. If no Chromium browser is present the
caller is told so and falls back to the terminal renderer -- the scanner never
depends on a window existing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from urllib.parse import urlparse
from pathlib import Path
from typing import Optional

# Order matters: Edge first, because it is the one guaranteed to be present on
# the Windows target.
WINDOWS_CANDIDATES = (
    r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
    r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe",
)

POSIX_CANDIDATES = (
    "microsoft-edge", "microsoft-edge-stable",
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
    "brave-browser",
)

MAC_CANDIDATES = (
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)

_LAUNCH_LOCK = threading.Lock()
_ACTIVE_PROCESS: Optional[subprocess.Popen] = None


def _valid_local_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        return (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}
                and parsed.port is not None and not parsed.username and not parsed.password)
    except ValueError:
        return False


def find_browser() -> Optional[str]:
    """Locate a Chromium-family browser, or nothing."""
    if sys.platform == "win32":
        for candidate in WINDOWS_CANDIDATES:
            path = Path(os.path.expandvars(candidate))
            if path.is_file():
                return str(path)
        return None
    if sys.platform == "darwin":
        for candidate in MAC_CANDIDATES:
            if Path(candidate).is_file():
                return candidate
    for name in POSIX_CANDIDATES:
        found = shutil.which(name)
        if found:
            return found
    return None


def launch(url: str, profile_dir: Optional[Path] = None, fullscreen: bool = True,
           browser: Optional[str] = None) -> Optional[subprocess.Popen]:
    """Open ``url`` in an app window. Returns the process, or nothing.

    A dedicated profile directory keeps the command center out of the
    operator's normal browsing session and stops a "restore pages?" prompt from
    ever appearing over the interface.
    """
    global _ACTIVE_PROCESS
    if not _valid_local_url(url):
        return None
    with _LAUNCH_LOCK:
        if _ACTIVE_PROCESS is not None and _ACTIVE_PROCESS.poll() is None:
            return _ACTIVE_PROCESS
    executable = browser or find_browser()
    if executable is None:
        return None

    arguments = [
        executable,
        f"--app={url}",
        "--new-window",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate,AutofillServerCommunication",
        "--autoplay-policy=no-user-gesture-required",
        "--start-maximized",
    ]
    if profile_dir is not None:
        arguments.append(f"--user-data-dir={profile_dir}")
    if fullscreen:
        arguments.extend(("--start-fullscreen", "--kiosk"))
        if "msedge" in Path(executable).name.lower():
            arguments.append("--edge-kiosk-type=fullscreen")

    creation_flags = 0
    if sys.platform == "win32":
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        process = subprocess.Popen(arguments, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL,
                                   creationflags=creation_flags)
        with _LAUNCH_LOCK:
            _ACTIVE_PROCESS = process
        return process
    except OSError:
        return None
