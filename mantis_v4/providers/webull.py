"""
MANTIS V4 — official Webull OpenAPI event-contract provider.

READ THIS BEFORE EXTENDING THIS FILE.

Master prompt section 26A is explicit about what is forbidden: do not scrape
hidden/private endpoints, do not reverse-engineer private authentication, do
not spoof clients, do not pretend an unofficial endpoint is the official public
API, and do not hard-code fake URLs. Nothing in this file does any of those.

What the Phase 1 audit CONFIRMED from official Webull documentation:

  * Event contracts are a documented first-class asset class on the official
    OpenAPI, with discovery endpoints named "Get Event Contract Categories",
    "Get Event Contract Series" and "Get Event Contract Instruments".
  * Event-contract market data endpoints exist and are recent:
      2026-01-31  "Event Snapshot" and "Event Depth" added
      2026-03-14  "Event Bars" and "Event Tick" added
      2026-03-28  real-time streaming for event contracts
  * Authentication is mandatory. There is no anonymous access. Clients are
    constructed with an App Key and App Secret; the SDK signs requests.
    The current market-data overview says event contracts require no additional
    subscription (subscriptions are required for several other products).
  * Order constraints: LIMIT + DAY only, instrument_type EVENT, event_outcome
    in {yes, no}, price range $0.01-$0.99.
  * The SDK repository ``webull-inc/openapi-python-sdk`` is ARCHIVED. The
    current one is ``webull-inc/webull-openapi-python-sdk``.

What still cannot be inferred from a generic response, and will not be guessed:

  1. whether crypto event contracts exist at a 15-MINUTE cadence, and on which
     underlyings;
  2. the exact settlement rule -- reference source, timestamp, TWAP vs
     point-in-time, tie handling;
  3. which discovered venue instrument maps to MANTIS's active contract;
  4. whether a receipt timestamp is authoritative enough for quote freshness.

The current official snapshot schema documents ``yes_bid``, ``yes_ask``,
``no_bid``, ``no_ask``, side sizes, volume, open interest and last-trade time.
The concrete request remains behind ``_fetch_contract_payload`` until live
credentials can validate instrument mapping and freshness semantics.
Writing a plausible-looking ``client.get_event_snapshot(...)`` call with
invented field names would be exactly the fabrication the master prompt
forbids, and it would fail silently or -- far worse -- succeed against the
wrong fields and produce confident garbage.

Everything AROUND that seam is real and tested: credential resolution, SDK
detection, health reporting, graceful degradation, and the guarantee that a
missing Webull integration never stops MANTIS.

TO COMPLETE THIS PROVIDER (a Phase 8 task, once credentials exist):
  1. resolve UNKNOWN-1..6 against the live official documentation;
  2. implement ``_fetch_contract_payload`` using only official SDK calls;
  3. implement ``_parse_contract_payload`` mapping verified field names;
  4. set ``settlement_rule`` to the VERIFIED rule from the specification --
     never to a proxy value;
  5. add a recorded-response fixture test so the schema cannot drift silently.
"""

from __future__ import annotations

from typing import Any, Optional

from ..clock import Instant
from ..config import WebullCredentials
from ..contracts import ContractSpec, ContractWindow
from .base import EventContractProvider, HealthState, ProviderUnavailable

# Official SDK distributions, in current-first order. Detection only -- MANTIS
# never installs anything itself.
CANDIDATE_SDK_MODULES = (
    "webull",                    # webull-inc/webull-openapi-python-sdk (current)
    "webullsdkmdata",            # webull-python-sdk-mdata  (archived line)
    "webullsdkcore",             # webull-python-sdk-core   (archived line)
)


def detect_official_sdk() -> Optional[str]:
    """Return the name of an installed official Webull SDK module, or None."""
    import importlib.util

    for module_name in CANDIDATE_SDK_MODULES:
        try:
            if importlib.util.find_spec(module_name) is not None:
                return module_name
        except (ImportError, ValueError):
            continue
    return None


class OfficialWebullOpenAPIProvider(EventContractProvider):
    """Preferred contract provider. Fully optional at runtime.

    Resolution order of its own state:
      no credentials        -> AUTH_NOT_CONFIGURED
      credentials, no SDK   -> SDK_NOT_INSTALLED
      market data disabled  -> UNAVAILABLE
      otherwise             -> SCHEMA_UNVERIFIED (see module docstring)

    In every one of those states ``get_contract_spec`` returns None and the
    provider chain falls through to the next adapter. MANTIS keeps running.
    """

    name = "webull-official"
    priority = 10

    def __init__(
        self,
        credentials: WebullCredentials,
        *,
        sdk_module: Optional[str] = None,
    ) -> None:
        super().__init__()
        self.credentials = credentials
        self._sdk_module = sdk_module if sdk_module is not None else detect_official_sdk()
        self._refresh_health()

    # -- state --------------------------------------------------------------

    def _refresh_health(self) -> None:
        if not self.credentials.is_configured:
            self.health.set_state(
                HealthState.AUTH_NOT_CONFIGURED,
                f"set {', '.join(('WEBULL_APP_KEY', 'WEBULL_APP_SECRET'))}",
            )
            return
        if self._sdk_module is None:
            self.health.set_state(
                HealthState.SDK_NOT_INSTALLED,
                "official Webull OpenAPI Python SDK not importable",
            )
            return
        if not self.credentials.market_data_enabled:
            self.health.set_state(
                HealthState.UNAVAILABLE,
                "WEBULL_MARKET_DATA_ENABLED is not set",
            )
            return
        self.health.set_state(HealthState.SCHEMA_UNVERIFIED,
            "documented quote schema; live instrument mapping not verified")

    @property
    def is_operational(self) -> bool:
        """True only when this provider could actually serve a spec."""
        return self.health.state.is_usable

    def status_lines(self) -> list[str]:
        """Status block for the UI (section 26A/26N)."""
        state = self.health.state
        if state is HealthState.AUTH_NOT_CONFIGURED:
            return [
                "WEBULL STATUS:        AUTH NOT CONFIGURED",
                "LIVE CONTRACT QUOTES: UNAVAILABLE",
                "EV ENGINE:            DISABLED",
            ]
        if state is HealthState.SDK_NOT_INSTALLED:
            return [
                "WEBULL STATUS:        SDK NOT INSTALLED",
                "LIVE CONTRACT QUOTES: UNAVAILABLE",
                "EV ENGINE:            DISABLED",
            ]
        if state is HealthState.SCHEMA_UNVERIFIED:
            return [
                "WEBULL STATUS:        CREDENTIALS OK / SCHEMA NOT VERIFIED",
                "LIVE CONTRACT QUOTES: UNAVAILABLE",
                "EV ENGINE:            DISABLED",
            ]
        if state.is_usable:
            return [
                "WEBULL STATUS:        LIVE",
                "LIVE CONTRACT QUOTES: AVAILABLE",
                "EV ENGINE:            SUBJECT TO QUOTE FRESHNESS",
            ]
        return [
            f"WEBULL STATUS:        {state.value}",
            "LIVE CONTRACT QUOTES: UNAVAILABLE",
            "EV ENGINE:            DISABLED",
        ]

    # -- the deliberate seam ------------------------------------------------

    def _fetch_contract_payload(
        self,
        asset: str,
        window: ContractWindow,
        instant: Instant,
    ) -> Any:
        """Fetch the raw event-contract payload from the official SDK.

        NOT IMPLEMENTED ON PURPOSE. See the module docstring: the response
        schema (audit UNKNOWN-4) has not been verified against real
        documentation with real credentials, and inventing field names would
        produce confidently wrong contract economics.
        """
        raise ProviderUnavailable(
            "Webull event-contract schema not verified. Resolve audit "
            "UNKNOWN-1..6 and implement _fetch_contract_payload using only "
            "official documented SDK calls."
        )

    def _parse_contract_payload(
        self,
        payload: Any,
        asset: str,
        window: ContractWindow,
    ) -> Optional[ContractSpec]:
        """Map a verified payload onto ContractSpec. Implement with (3) above."""
        raise ProviderUnavailable("Webull event-contract schema not verified.")

    # -- interface ----------------------------------------------------------

    def get_contract_spec(
        self,
        asset: str,
        window: ContractWindow,
        instant: Instant,
    ) -> Optional[ContractSpec]:
        """Return a spec, or None. Never raises into the market loop."""
        if not self.is_operational:
            return None
        try:
            payload = self._fetch_contract_payload(asset, window, instant)
            return self._parse_contract_payload(payload, asset, window)
        except ProviderUnavailable as exc:
            self.health.set_state(HealthState.SCHEMA_UNVERIFIED, str(exc)[:120])
            return None
        except Exception as exc:  # noqa: BLE001 - section 26N: never crash the loop
            self.health.record_failure(f"{type(exc).__name__}: {exc}")
            return None
