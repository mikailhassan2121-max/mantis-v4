"""
MANTIS V4 — configuration architecture.

Phase 2 fixes for audit findings F-1 (21 of ~45 V3 constants were inert) and
the credential-handling requirements of master prompt section 26A.

Rules enforced here:

1.  EVERY setting in this file is wired to real behaviour. V3 shipped a config
    block where nearly half the knobs did nothing -- including
    ``MIN_ENTRY_STRENGTH = 80.0``, which reads like a live safety gate and was
    referenced exactly once, at its own definition. A knob that does nothing is
    worse than no knob, because it invites tuning that has no effect.
    ``python -m mantis_v4.config --audit`` re-verifies this claim.

2.  Secrets are NEVER hard-coded, NEVER logged, and NEVER committed. They come
    from environment variables or a gitignored local file, in that precedence.
    ``MantisConfig.__repr__`` redacts them.

3.  Thresholds that Phase 2 is not allowed to tune are marked PHASE-DEFERRED
    and carry V3's value verbatim, so Phase 2 changes no decision behaviour.

Precedence (later wins):
    dataclass defaults  ->  config/mantis_v4.local.json  ->  environment
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Optional

# Repository root = parent of the mantis_v4 package directory.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_LOCAL_CONFIG = PROJECT_ROOT / "config" / "mantis_v4.local.json"

# Environment variable names. Documented here so there is exactly one list.
ENV_APP_KEY = "WEBULL_APP_KEY"
ENV_APP_SECRET = "WEBULL_APP_SECRET"
ENV_MARKET_DATA_ENABLED = "WEBULL_MARKET_DATA_ENABLED"
ENV_REGION = "WEBULL_REGION"
ENV_LOCAL_CONFIG = "MANTIS_V4_CONFIG"


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


@dataclass
class WebullCredentials:
    """Webull OpenAPI credentials. Never printed, never persisted by MANTIS.

    Master prompt section 26A: the official API requires an App Key and App
    Secret; there is no anonymous access, and market data additionally requires
    a separate OpenAPI subscription. MANTIS must run fine without any of it.
    """

    app_key: Optional[str] = None
    app_secret: Optional[str] = None
    market_data_enabled: bool = False
    region: str = "us"

    @property
    def is_configured(self) -> bool:
        return bool(self.app_key) and bool(self.app_secret)

    def __repr__(self) -> str:
        """Redacted. Prevents accidental credential leakage into logs/tracebacks."""
        key = "SET" if self.app_key else "UNSET"
        secret = "SET" if self.app_secret else "UNSET"
        return (
            f"WebullCredentials(app_key=<{key}>, app_secret=<{secret}>, "
            f"market_data_enabled={self.market_data_enabled}, region={self.region!r})"
        )

    __str__ = __repr__


@dataclass
class MantisConfig:
    """Complete V4 runtime configuration.

    Field groups are ordered by subsystem. Anything a future phase will tune is
    marked; Phase 2 must not change decision thresholds.
    """

    # -- universe -----------------------------------------------------------
    # Master prompt section 26J adds ADA and asks that XRP be preserved as
    # configurable rather than deleted. Only enabled assets are displayed.
    assets: list[str] = field(
        default_factory=lambda: ["BTC-USD", "ETH-USD", "SOL-USD", "ADA-USD", "XRP-USD"]
    )
    enabled_assets: Optional[list[str]] = None  # None => all of `assets`

    # -- contract semantics -------------------------------------------------
    contract_timezone: str = "America/New_York"
    contract_window_minutes: int = 15

    # -- loop ---------------------------------------------------------------
    # Section 26K asks for an approximately 5-second refresh with monotonic
    # drift compensation. V3 used a flat 10s sleep after variable work.
    scan_interval_seconds: float = 5.0
    network_timeout_seconds: float = 8.0
    max_retries: int = 3
    retry_backoff_seconds: float = 1.5

    # -- market data --------------------------------------------------------
    bar_interval: str = "1m"
    bootstrap_period: str = "7d"
    refresh_period: str = "1d"
    min_candles: int = 60
    max_cache_rows: int = 5000

    # -- data-quality gates (master prompt section 18) ----------------------
    # V3 allowed a 5-minute-old price to drive a 15-minute contract (audit D-2:
    # that is 33% of the contract's life). Tightened, and now surfaced in the UI
    # rather than being silent.
    max_data_age_seconds: float = 90.0
    max_quote_age_seconds: float = 15.0
    # Audit B-5: a frozen feed collapses realised volatility toward the floor and
    # manufactures near-maximum confidence. These detect a non-moving price even
    # when its timestamp looks current.
    stale_price_min_distinct_closes: int = 5
    stale_price_lookback_bars: int = 20
    min_realized_vol_1m: float = 1e-6

    # -- decision thresholds (PHASE-DEFERRED) -------------------------------
    # Carried verbatim from V3 so Phase 2 changes no decision behaviour.
    # Phase 7 owns these; they must not be tuned before calibration exists.
    min_model_confidence: float = 0.80         # V3 MIN_RESOLUTION_CONFIDENCE
    min_entry_elapsed_seconds: float = 150.0   # V3 MIN_ENTRY_ELAPSED_MINUTES 2.5
    no_new_entry_seconds: float = 45.0         # V3 NO_NEW_ENTRY_MINUTES 0.75
    force_exit_seconds: float = 7.2            # V3 FORCE_EXIT_MINUTES 0.12
    emergency_opposite_confidence: float = 0.82

    # -- EV gates (inert until real economics exist; section 3) -------------
    min_model_edge: float = 0.20               # section 26H baseline: 20pp
    min_expected_value: float = 0.0
    max_spread: float = 0.10

    # -- recording ----------------------------------------------------------
    record_every_n_scans: int = 1              # section 17: store every prediction
    resolution_grace_seconds: float = 120.0    # wait for the terminal bar to land
    resolution_max_attempts: int = 20

    # -- output paths (master prompt section 23) ----------------------------
    data_dir: str = "data"
    log_csv: str = "mantis_v4_log.csv"
    ledger_csv: str = "mantis_v4_ledger.csv"
    ledger_db: str = "mantis_v4_ledger.db"
    predictions_csv: str = "mantis_v4_predictions.csv"
    model_metrics_json: str = "mantis_v4_model_metrics.json"

    # -- providers ----------------------------------------------------------
    local_contract_dir: str = "config/contracts"
    credentials: WebullCredentials = field(default_factory=WebullCredentials)

    # -- ui / audio ---------------------------------------------------------
    voice_enabled: bool = True
    plain_cmd_mode: bool = False               # section 26K plain-CMD fallback

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------

    @property
    def active_assets(self) -> list[str]:
        if self.enabled_assets is None:
            return list(self.assets)
        return [a for a in self.assets if a in set(self.enabled_assets)]

    def path(self, filename: str) -> Path:
        """Resolve an output file inside the data directory, creating it."""
        base = PROJECT_ROOT / self.data_dir
        base.mkdir(parents=True, exist_ok=True)
        return base / filename

    @property
    def ledger_db_path(self) -> Path:
        return self.path(self.ledger_db)

    @property
    def predictions_csv_path(self) -> Path:
        return self.path(self.predictions_csv)

    @property
    def contracts_csv_path(self) -> Path:
        return self.path("mantis_v4_contracts.csv")

    @property
    def local_contract_path(self) -> Path:
        return PROJECT_ROOT / self.local_contract_dir

    def __repr__(self) -> str:
        parts = []
        for f in fields(self):
            value = getattr(self, f.name)
            parts.append(f"{f.name}={value!r}")   # credentials redact themselves
        return "MantisConfig(" + ", ".join(parts) + ")"

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    @classmethod
    def load(
        cls,
        local_file: Optional[Path] = None,
        environ: Optional[dict] = None,
    ) -> "MantisConfig":
        """Build a config from defaults, then a local file, then environment.

        Missing local file is normal and silent. A malformed local file is an
        error -- silently ignoring a config the user believes is active would be
        the same class of bug as V3's fail-open data-age check (audit D-6).
        """
        environ = os.environ if environ is None else environ
        config = cls()

        candidate = local_file
        if candidate is None:
            override = environ.get(ENV_LOCAL_CONFIG)
            candidate = Path(override) if override else DEFAULT_LOCAL_CONFIG

        if candidate and Path(candidate).exists():
            try:
                raw = json.loads(Path(candidate).read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                raise ValueError(
                    f"Local config {candidate} exists but could not be read: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
            config._apply_mapping(raw)

        config._apply_environment(environ)
        config.validate()
        return config

    def _apply_mapping(self, raw: dict) -> None:
        known = {f.name for f in fields(self)}
        creds = raw.get("credentials")
        for key, value in raw.items():
            if key == "credentials":
                continue
            if key not in known:
                raise ValueError(
                    f"Unknown configuration key {key!r} in local config file. "
                    "Refusing to ignore it silently."
                )
            setattr(self, key, value)
        if isinstance(creds, dict):
            self.credentials = WebullCredentials(
                app_key=creds.get("app_key") or None,
                app_secret=creds.get("app_secret") or None,
                market_data_enabled=_as_bool(creds.get("market_data_enabled")),
                region=creds.get("region") or "us",
            )

    def _apply_environment(self, environ: dict) -> None:
        """Environment wins over the local file. Secrets are env-first by design."""
        app_key = environ.get(ENV_APP_KEY) or self.credentials.app_key
        app_secret = environ.get(ENV_APP_SECRET) or self.credentials.app_secret

        if ENV_MARKET_DATA_ENABLED in environ:
            market_data = _as_bool(environ[ENV_MARKET_DATA_ENABLED])
        else:
            market_data = self.credentials.market_data_enabled

        region = environ.get(ENV_REGION) or self.credentials.region

        self.credentials = WebullCredentials(
            app_key=app_key or None,
            app_secret=app_secret or None,
            market_data_enabled=market_data,
            region=region,
        )

    def validate(self) -> None:
        """Reject impossible configurations at startup, not at 3am mid-trade."""
        if self.contract_window_minutes <= 0:
            raise ValueError("contract_window_minutes must be positive")
        if 60 % self.contract_window_minutes != 0:
            raise ValueError(
                "contract_window_minutes must divide an hour evenly so windows "
                "align to the clock"
            )
        if not self.active_assets:
            raise ValueError("No active assets configured")
        if self.scan_interval_seconds <= 0:
            raise ValueError("scan_interval_seconds must be positive")
        if self.max_data_age_seconds <= 0:
            raise ValueError("max_data_age_seconds must be positive")
        if not (0.0 < self.min_model_confidence < 1.0):
            raise ValueError("min_model_confidence must be strictly between 0 and 1")
        if self.record_every_n_scans < 1:
            raise ValueError("record_every_n_scans must be >= 1")


def credential_status_lines(config: MantisConfig) -> list[str]:
    """The exact status block the master prompt requires (section 26A/26N)."""
    creds = config.credentials
    if not creds.is_configured:
        return [
            "WEBULL STATUS:        AUTH NOT CONFIGURED",
            "LIVE CONTRACT QUOTES: UNAVAILABLE",
            "EV ENGINE:            DISABLED",
        ]
    if not creds.market_data_enabled:
        return [
            "WEBULL STATUS:        CREDENTIALS PRESENT / MARKET DATA NOT ENABLED",
            "LIVE CONTRACT QUOTES: UNAVAILABLE",
            "EV ENGINE:            DISABLED",
        ]
    return [
        "WEBULL STATUS:        CREDENTIALS PRESENT / MARKET DATA ENABLED",
        "LIVE CONTRACT QUOTES: SUBJECT TO PROVIDER HEALTH",
        "EV ENGINE:            SUBJECT TO VERIFIED CONTRACT SPEC",
    ]
