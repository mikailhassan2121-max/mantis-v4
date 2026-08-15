"""Phase 9 presentation settings.

Presentation configuration is deliberately separate from ``MantisConfig``.
``MantisConfig`` owns quantitative and provider behaviour; nothing in this file
may influence a decision. Every field here changes only what the operator sees
or hears.

Precedence (later wins):
    dataclass defaults  ->  config/mantis_v4.ui.json  ->  environment  ->  CLI flags

Environment variables are the field name upper-cased with a ``MANTIS_UI_``
prefix, e.g. ``MANTIS_UI_VOICE_ENABLED=0``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_UI_CONFIG = PROJECT_ROOT / "config" / "mantis_v4.ui.json"

ENV_PREFIX = "MANTIS_UI_"
ENV_UI_CONFIG = "MANTIS_V4_UI_CONFIG"

TRUE_WORDS = {"1", "true", "yes", "on", "enabled"}
FALSE_WORDS = {"0", "false", "no", "off", "disabled"}


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in TRUE_WORDS:
        return True
    if text in FALSE_WORDS:
        return False
    raise ValueError(f"cannot interpret {value!r} as a boolean")


@dataclass
class PresentationConfig:
    """Everything the operator can tune about presentation, audio and voice."""

    # -- interface ----------------------------------------------------------
    ui_enabled: bool = True
    ui_mode: str = "web"                    # "web" (eDEX-style shell) or "terminal" (Rich)
    ui_refresh_rate: float = 4.0            # renders per second (countdown smoothness)
    startup_animation: bool = True

    # -- web shell ----------------------------------------------------------
    web_host: str = "127.0.0.1"             # never bind a routable interface
    web_port: int = 0                       # 0 = let the OS choose a free port
    web_open_browser: bool = True           # launch Edge/Chrome in app mode
    web_fullscreen: bool = True
    scanlines_enabled: bool = True
    background_grid_enabled: bool = True

    # -- boot sequence ------------------------------------------------------
    boot_sequence_enabled: bool = True
    boot_duration: float = 15.0             # cinematic prelude + boot + shell assembly
    boot_audio_enabled: bool = True
    startup_alert_suppression_seconds: float = 16.0  # gate operational tones/speech
    show_advanced_diagnostics: bool = False
    default_focused_asset: str = "BTC-USD"
    event_log_length: int = 200             # in-memory cap; disk logs stay append-only
    event_log_visible_rows: int = 8
    minimum_terminal_width: int = 96
    minimum_terminal_height: int = 30
    show_forward_validation: bool = True
    forward_report_interval_seconds: float = 60.0

    # -- audio --------------------------------------------------------------
    audio_enabled: bool = True
    master_volume: float = 0.7              # 0.0 - 1.0, scales tone duration/level
    audio_enter_yes: bool = True
    audio_enter_no: bool = True
    audio_wait: bool = False                # WAIT is silent by design
    audio_data_hold: bool = True
    audio_rollover: bool = True
    audio_resolution: bool = True
    audio_error: bool = True
    audio_min_interval_seconds: float = 15.0  # anti alarm-fatigue floor per cue+asset

    # -- voice --------------------------------------------------------------
    voice_enabled: bool = True
    voice_rate: int = 1                     # System.Speech rate, -10..10
    voice_volume: int = 90                  # System.Speech volume, 0..100
    voice_enter: bool = True
    voice_data_hold: bool = True
    voice_rollover: bool = False            # rollovers are frequent: log/tone only
    voice_resolution: bool = True
    voice_error: bool = True
    voice_min_interval_seconds: float = 30.0

    # -- diagnostics --------------------------------------------------------
    diagnostic_log: str = "data/logs/mantis_ui_diagnostics.log"
    diagnostic_log_max_bytes: int = 2_000_000
    diagnostic_log_backups: int = 3
    developer_mode: bool = False            # show raw tracebacks in-band

    # -- modes (set by CLI, never persisted as "real") ----------------------
    demo_mode: bool = False

    _sources: list[str] = field(default_factory=list, repr=False)

    # -- derived ------------------------------------------------------------

    @property
    def refresh_interval(self) -> float:
        return 1.0 / self.ui_refresh_rate

    def diagnostic_log_path(self) -> Path:
        path = Path(self.diagnostic_log)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path

    # -- loading ------------------------------------------------------------

    @classmethod
    def load(
        cls,
        local_file: Optional[Path] = None,
        environ: Optional[dict] = None,
    ) -> "PresentationConfig":
        """Defaults, then the UI JSON file, then environment.

        A missing file is normal. A malformed or unknown-key file is an error:
        a presentation setting the operator believes is active but is silently
        ignored is the same class of bug the quant config already refuses.
        """
        environ = os.environ if environ is None else environ
        config = cls()

        candidate = local_file
        if candidate is None:
            override = environ.get(ENV_UI_CONFIG)
            candidate = Path(override) if override else DEFAULT_UI_CONFIG

        if candidate and Path(candidate).exists():
            try:
                raw = json.loads(Path(candidate).read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                raise ValueError(
                    f"UI config {candidate} exists but could not be read: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
            config._apply_mapping(raw)
            config._sources.append(str(candidate))

        config._apply_environment(environ)
        config.validate()
        return config

    def _coerce(self, name: str, value: Any) -> Any:
        declared = {f.name: f.type for f in fields(self)}[name]
        if declared == "bool" or isinstance(getattr(self, name), bool):
            return _as_bool(value)
        if isinstance(getattr(self, name), int):
            return int(value)
        if isinstance(getattr(self, name), float):
            return float(value)
        return str(value)

    def _apply_mapping(self, raw: dict) -> None:
        known = {f.name for f in fields(self) if not f.name.startswith("_")}
        for key, value in raw.items():
            name = key.lower()
            if name.startswith("_"):
                continue                      # reserved for comments in the example file
            if name not in known:
                raise ValueError(
                    f"Unknown presentation setting {key!r}. Refusing to ignore it silently."
                )
            setattr(self, name, self._coerce(name, value))

    def _apply_environment(self, environ: dict) -> None:
        for f in fields(self):
            if f.name.startswith("_"):
                continue
            key = ENV_PREFIX + f.name.upper()
            if key in environ:
                setattr(self, f.name, self._coerce(f.name, environ[key]))
                self._sources.append(key)

    def validate(self) -> "PresentationConfig":
        if not 0.2 <= self.ui_refresh_rate <= 30.0:
            raise ValueError("ui_refresh_rate must be between 0.2 and 30 renders/second")
        if not 0.0 <= self.master_volume <= 1.0:
            raise ValueError("master_volume must be between 0.0 and 1.0")
        if self.event_log_length < 1:
            raise ValueError("event_log_length must be >= 1")
        if self.event_log_visible_rows < 1:
            raise ValueError("event_log_visible_rows must be >= 1")
        if not -10 <= self.voice_rate <= 10:
            raise ValueError("voice_rate must be between -10 and 10")
        if not 0 <= self.voice_volume <= 100:
            raise ValueError("voice_volume must be between 0 and 100")
        if self.minimum_terminal_width < 40 or self.minimum_terminal_height < 10:
            raise ValueError("minimum terminal dimensions are unusably small")
        if self.ui_mode not in ("web", "terminal"):
            raise ValueError("ui_mode must be 'web' or 'terminal'")
        if not 0 <= self.web_port <= 65535:
            raise ValueError("web_port must be between 0 and 65535")
        if not 0.0 <= self.boot_duration <= 30.0:
            raise ValueError("boot_duration must be between 0 and 30 seconds")
        if not 0.0 <= self.startup_alert_suppression_seconds <= 60.0:
            raise ValueError("startup_alert_suppression_seconds must be between 0 and 60 seconds")
        if self.diagnostic_log_max_bytes < 16_384:
            raise ValueError("diagnostic_log_max_bytes must be >= 16384")
        if not 1 <= self.diagnostic_log_backups <= 10:
            raise ValueError("diagnostic_log_backups must be between 1 and 10")
        diagnostic = Path(self.diagnostic_log)
        if not diagnostic.is_absolute() and ".." in diagnostic.parts:
            raise ValueError("diagnostic_log must not escape the project directory")
        return self

    def apply_profile(self, profile: Optional[str]) -> "PresentationConfig":
        """Apply one small operator profile; no quantitative setting is present."""
        name = (profile or "default").lower()
        if name == "default":
            return self
        if name == "quiet":
            self.audio_enabled = False
            self.voice_enabled = False
        elif name == "diagnostic":
            self.show_advanced_diagnostics = True
            self.developer_mode = True
        else:
            raise ValueError(f"unknown presentation profile {profile!r}")
        return self.validate()

    def apply_cli(self, args: Any) -> "PresentationConfig":
        """Apply argparse flags. CLI always wins over file and environment."""
        self.apply_profile(getattr(args, "profile", None))
        if getattr(args, "no_ui", False):
            self.ui_enabled = False
        if getattr(args, "no_audio", False):
            self.audio_enabled = False
        if getattr(args, "no_voice", False):
            self.voice_enabled = False
        if getattr(args, "diagnostics", False):
            self.show_advanced_diagnostics = True
        if getattr(args, "no_startup", False):
            self.startup_animation = False
            self.boot_sequence_enabled = False
            self.startup_alert_suppression_seconds = 0.0
        if getattr(args, "demo", False):
            self.demo_mode = True
        if getattr(args, "ui", None):
            self.ui_mode = args.ui
        if getattr(args, "no_browser", False):
            self.web_open_browser = False
        if getattr(args, "port", None):
            self.web_port = int(args.port)
        if getattr(args, "focus", None):
            self.default_focused_asset = args.focus
        return self.validate()

    def audio_allows(self, event_key: str) -> bool:
        return self.audio_enabled and bool(getattr(self, f"audio_{event_key}", False))

    def voice_allows(self, event_key: str) -> bool:
        return self.voice_enabled and bool(getattr(self, f"voice_{event_key}", False))
