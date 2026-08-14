"""
MANTIS V4 — local/manual contract-specification provider.

Master prompt section 2 requires that contract data be pluggable from "a local
JSON feed / a local CSV feed / a manually updated bridge". This is that
adapter, and it is fully implemented -- it is the escape hatch that lets a user
supply a REAL strike and REAL quotes read off their own Webull screen, without
credentials and without any scraping.

Important honesty constraint: a manually entered reference is NOT a verified
contract specification. ``ReferenceSource.MANUAL_LOCAL.is_verified`` is False,
so a manual reference alone will not unlock EV claims. To unlock economics the
user must additionally declare an explicit settlement rule, which is their
attestation that they read it off the real contract. That is a deliberate
speed bump: it makes "I told MANTIS the strike" and "MANTIS verified the
strike" two visibly different things.

File format (JSON), one file per window or a directory of them:

    {
      "contracts": [
        {
          "asset": "BTC-USD",
          "window_start_utc": "2026-08-13T15:00:00Z",
          "reference_price": 63400.0,
          "settlement_rule": "TERMINAL_ABOVE_REFERENCE",
          "venue_contract_id": "...",
          "yes_bid": 0.54, "yes_ask": 0.55,
          "no_bid":  0.44, "no_ask":  0.46,
          "quote_timestamp": "2026-08-13T15:04:11Z"
        }
      ]
    }

CSV format: the same column names as the JSON keys, one row per contract.

Every timestamp must be timezone-aware. A naive timestamp is rejected rather
than assumed to be UTC or local (audit finding D-8).
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..clock import Instant, parse_iso_utc
from ..contracts import (
    ContractQuote,
    ContractSpec,
    ContractWindow,
    ReferenceSource,
    SettlementRule,
)
from .base import EventContractProvider, HealthState


def _to_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


class LocalFileContractProvider(EventContractProvider):
    """Reads contract specs from JSON/CSV files in a directory.

    Files are re-read when their mtime changes, so a user can update quotes
    mid-window and MANTIS picks them up on the next scan.
    """

    name = "local-file"
    priority = 50

    def __init__(self, directory: Path) -> None:
        super().__init__()
        self.directory = Path(directory)
        self._cache: dict[str, ContractSpec] = {}
        self._signature: Optional[tuple] = None
        self._refresh_health()

    # -- loading ------------------------------------------------------------

    def _directory_signature(self) -> tuple:
        """Cheap change detector: (path, mtime, size) for every readable file."""
        if not self.directory.exists():
            return ()
        entries = []
        for path in sorted(self.directory.glob("*")):
            if path.suffix.lower() not in {".json", ".csv"}:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            entries.append((str(path), stat.st_mtime_ns, stat.st_size))
        return tuple(entries)

    def _refresh_health(self) -> None:
        if not self.directory.exists():
            self.health.set_state(
                HealthState.UNAVAILABLE, f"no directory {self.directory}"
            )
            return
        signature = self._directory_signature()
        if not signature:
            self.health.set_state(
                HealthState.UNAVAILABLE, f"no .json/.csv files in {self.directory}"
            )

    def _iter_records(self) -> Iterable[dict]:
        for path in sorted(self.directory.glob("*")):
            suffix = path.suffix.lower()
            try:
                if suffix == ".json":
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    records = payload.get("contracts", []) if isinstance(payload, dict) else payload
                    if isinstance(records, list):
                        yield from (r for r in records if isinstance(r, dict))
                elif suffix == ".csv":
                    with path.open("r", newline="", encoding="utf-8") as handle:
                        yield from csv.DictReader(handle)
            except (OSError, json.JSONDecodeError, csv.Error) as exc:
                self.health.record_failure(f"{path.name}: {type(exc).__name__}: {exc}")
                continue

    def _build_spec(self, record: dict) -> Optional[ContractSpec]:
        asset = (record.get("asset") or "").strip()
        if not asset:
            return None

        start = parse_iso_utc(str(record.get("window_start_utc", "")))
        if start is None:
            # A naive or missing timestamp is rejected, never guessed.
            return None

        window_minutes = int(_to_float(record.get("window_minutes")) or 15)
        tz_name = (record.get("timezone") or "America/New_York").strip()
        try:
            tz = ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            return None
        window = ContractWindow.for_instant(start, tz, window_minutes)

        reference = _to_float(record.get("reference_price"))

        rule_text = (record.get("settlement_rule") or "").strip().upper()
        try:
            rule = SettlementRule[rule_text] if rule_text else SettlementRule.UNKNOWN
        except KeyError:
            rule = SettlementRule.UNKNOWN

        quote = ContractQuote(
            yes_bid=_to_float(record.get("yes_bid")),
            yes_ask=_to_float(record.get("yes_ask")),
            no_bid=_to_float(record.get("no_bid")),
            no_ask=_to_float(record.get("no_ask")),
            quote_timestamp=parse_iso_utc(str(record.get("quote_timestamp", ""))),
            source=self.name,
        )

        return ContractSpec(
            window=window,
            underlying=asset,
            reference_price=reference,
            reference_source=(
                ReferenceSource.MANUAL_LOCAL if reference is not None
                else ReferenceSource.UNAVAILABLE
            ),
            settlement_rule=rule,
            quote=quote,
            provider_name=self.name,
            venue_contract_id=(record.get("venue_contract_id") or None),
            venue_symbol=(record.get("venue_symbol") or None),
            notes="user-supplied local contract specification",
        )

    def _reload_if_changed(self, instant: Instant) -> None:
        signature = self._directory_signature()
        if signature == self._signature and self._cache:
            return
        self._signature = signature

        if not signature:
            self._cache = {}
            self._refresh_health()
            return

        cache: dict[str, ContractSpec] = {}
        for record in self._iter_records():
            spec = self._build_spec(record)
            if spec is not None:
                cache[spec.key] = spec
        self._cache = cache

        if cache:
            self.health.record_success(instant.utc, detail=f"{len(cache)} contracts")
        else:
            self.health.set_state(
                HealthState.UNAVAILABLE, "no usable contract records"
            )

    # -- interface ----------------------------------------------------------

    def get_contract_spec(
        self,
        asset: str,
        window: ContractWindow,
        instant: Instant,
    ) -> Optional[ContractSpec]:
        try:
            self._reload_if_changed(instant)
        except Exception as exc:  # noqa: BLE001 - never crash the loop
            self.health.record_failure(f"{type(exc).__name__}: {exc}")
            return None
        return self._cache.get(window.key_for(asset))
