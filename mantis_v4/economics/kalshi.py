"""Read-only Kalshi public REST event-market quote provider.

This module is intentionally *not* an ``EconomicsProvider``. Step 2 proves
mapping and quote semantics only; no object here can reach Phase 7, the primary
selector, forward records, credentials, or an order endpoint.
"""
from __future__ import annotations

import json
import socket
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Callable, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

UTC = timezone.utc
KALSHI_BASE_URL = "https://external-api.kalshi.com/trade-api/v2"
KALSHI_PROVIDER = "KALSHI_PUBLIC_REST"
KALSHI_SETTLEMENT_SOURCE = "CF_BENCHMARKS"
KALSHI_SETTLEMENT_PROVENANCE = "KALSHI_CRYPTO15M_RULES"
ONE = Decimal("1.0000")
SERIES_BY_ASSET = {
    "BTC-USD": "KXBTC15M",
    "ETH-USD": "KXETH15M",
    "SOL-USD": "KXSOL15M",
    "XRP-USD": "KXXRP15M",
}


class KalshiStatus(str, Enum):
    READY = "READY"
    UNSUPPORTED_ASSET = "UNSUPPORTED_ASSET"
    MARKET_INITIALIZING = "MARKET_INITIALIZING"
    MARKET_UNAVAILABLE = "MARKET_UNAVAILABLE"
    MAPPING_AMBIGUOUS = "MAPPING_AMBIGUOUS"
    MAPPING_INVALID = "MAPPING_INVALID"
    BOOK_SIDE_MISSING = "BOOK_SIDE_MISSING"
    QUOTE_STALE = "QUOTE_STALE"
    RATE_LIMITED = "RATE_LIMITED"
    TEMPORARY_FAILURE = "TEMPORARY_FAILURE"
    TIMEOUT = "TIMEOUT"
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"


class KalshiError(RuntimeError):
    status = KalshiStatus.TEMPORARY_FAILURE


class KalshiHTTPError(KalshiError):
    def __init__(self, code: int):
        super().__init__(f"HTTP {code}")
        self.code = code
        self.status = KalshiStatus.RATE_LIMITED if code == 429 else KalshiStatus.TEMPORARY_FAILURE


class KalshiTimeout(KalshiError):
    status = KalshiStatus.TIMEOUT


class KalshiMalformedResponse(KalshiError):
    status = KalshiStatus.MALFORMED_RESPONSE


@dataclass(frozen=True)
class KalshiMarketMapping:
    asset: str
    series_ticker: str
    event_ticker: str
    market_ticker: str
    title: str
    status: str
    window_start_utc: datetime
    window_end_utc: datetime
    close_time_utc: datetime
    expected_expiration_time_utc: Optional[datetime]
    strike_type: str
    target: Decimal
    settlement_source: str
    settlement_reference_provenance: str
    price_structure: str
    mapping_verified: bool
    mapping_timestamp_utc: datetime


@dataclass(frozen=True)
class KalshiEventQuote:
    asset: str
    market_ticker: str
    event_ticker: str
    series_ticker: str
    yes_bid: Optional[Decimal]
    yes_bid_size: Optional[Decimal]
    no_bid: Optional[Decimal]
    no_bid_size: Optional[Decimal]
    yes_ask: Optional[Decimal]
    yes_ask_size: Optional[Decimal]
    no_ask: Optional[Decimal]
    no_ask_size: Optional[Decimal]
    quote_timestamp_utc: datetime
    quote_timestamp_provenance: str
    received_at_utc: datetime
    quote_age_seconds: Decimal
    market_status: str
    provider: str
    authenticated: bool
    mapping_verified: bool
    quote_verified: bool
    settlement_source: str
    settlement_reference_provenance: str
    target: Decimal
    window_start_utc: datetime
    window_end_utc: datetime

    def fresh_at(self, instant: datetime, max_age_seconds: float) -> bool:
        return Decimal(str(max(0.0, (_utc(instant) - self.quote_timestamp_utc).total_seconds()))) <= Decimal(str(max_age_seconds))


@dataclass(frozen=True)
class KalshiQuoteResult:
    status: KalshiStatus
    mapping: Optional[KalshiMarketMapping] = None
    quote: Optional[KalshiEventQuote] = None
    detail: str = ""


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _parse_time(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise KalshiMalformedResponse("invalid timestamp") from exc
    return _utc(parsed)


def _decimal(value, label: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise KalshiMalformedResponse(f"invalid {label}") from exc
    if not result.is_finite():
        raise KalshiMalformedResponse(f"non-finite {label}")
    return result


def _best(levels, side: str):
    if levels is None or not isinstance(levels, list):
        raise KalshiMalformedResponse(f"{side} levels missing")
    parsed = []
    for level in levels:
        if not isinstance(level, list) or len(level) != 2:
            raise KalshiMalformedResponse(f"invalid {side} level")
        price, quantity = _decimal(level[0], f"{side} price"), _decimal(level[1], f"{side} quantity")
        if not Decimal("0") < price < ONE or quantity <= 0:
            raise KalshiMalformedResponse(f"invalid {side} price/quantity range")
        parsed.append((price, quantity))
    return max(parsed, key=lambda item: item[0]) if parsed else None


def parse_orderbook(payload: dict):
    """Return defensive top-of-book values, preserving Decimal precision."""
    if not isinstance(payload, dict) or not isinstance(payload.get("orderbook_fp"), dict):
        raise KalshiMalformedResponse("orderbook_fp missing")
    book = payload["orderbook_fp"]
    yes, no = _best(book.get("yes_dollars"), "YES"), _best(book.get("no_dollars"), "NO")
    yes_bid, yes_size = yes if yes else (None, None)
    no_bid, no_size = no if no else (None, None)
    yes_ask = ONE - no_bid if no_bid is not None else None
    no_ask = ONE - yes_bid if yes_bid is not None else None
    if yes_ask is not None and not Decimal("0") < yes_ask <= ONE:
        raise KalshiMalformedResponse("derived YES ask out of range")
    if no_ask is not None and not Decimal("0") < no_ask <= ONE:
        raise KalshiMalformedResponse("derived NO ask out of range")
    return {"yes_bid": yes_bid, "yes_bid_size": yes_size,
            "no_bid": no_bid, "no_bid_size": no_size,
            "yes_ask": yes_ask, "yes_ask_size": no_size,
            "no_ask": no_ask, "no_ask_size": yes_size}


class KalshiPublicHttpClient:
    """Small anonymous JSON GET client with bounded transient retries."""
    def __init__(self, *, base_url=KALSHI_BASE_URL, timeout_seconds=5.0,
                 max_attempts=3, backoff_seconds=0.25,
                 transport: Optional[Callable] = None, sleep=time.sleep):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = float(timeout_seconds)
        self.max_attempts = int(max_attempts)
        self.backoff_seconds = float(backoff_seconds)
        self.transport = transport or self._stdlib_transport
        self.sleep = sleep
        if self.timeout_seconds <= 0 or not 1 <= self.max_attempts <= 5 or self.backoff_seconds < 0:
            raise ValueError("invalid Kalshi HTTP settings")

    @staticmethod
    def _stdlib_transport(url, timeout):
        request = Request(url, headers={"Accept": "application/json", "User-Agent": "MANTIS-Kalshi-ReadOnly/1.0"}, method="GET")
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.status, response.read()
        except HTTPError as exc:
            raise KalshiHTTPError(exc.code) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise KalshiTimeout("request timed out") from exc
        except URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise KalshiTimeout("request timed out") from exc
            raise KalshiError(str(exc.reason)) from exc

    def get(self, path: str, **params) -> dict:
        if not path.startswith("/") or "/portfolio" in path or "order" in path.lower() and "orderbook" not in path.lower():
            raise ValueError("Kalshi client permits documented public market-data GET paths only")
        url = self.base_url + path + (("?" + urlencode(params)) if params else "")
        last = None
        for attempt in range(self.max_attempts):
            try:
                response = self.transport(url, self.timeout_seconds)
                if isinstance(response, dict):
                    return response
                status, raw = response
                if status == 429 or 500 <= status <= 599:
                    raise KalshiHTTPError(status)
                if status != 200:
                    raise KalshiHTTPError(status)
                try:
                    body = json.loads(raw)
                except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
                    raise KalshiMalformedResponse("invalid JSON") from exc
                if not isinstance(body, dict):
                    raise KalshiMalformedResponse("JSON root is not an object")
                return body
            except (KalshiTimeout, KalshiHTTPError, KalshiError) as exc:
                last = exc
                transient = isinstance(exc, KalshiTimeout) or (isinstance(exc, KalshiHTTPError) and (exc.code == 429 or exc.code >= 500))
                if not transient or attempt + 1 >= self.max_attempts:
                    raise
                self.sleep(self.backoff_seconds * (2 ** attempt))
        raise last or KalshiError("request failed")


class KalshiEventMarketProvider:
    """Discovers exact active-window mappings and returns fresh public books."""
    provider = KALSHI_PROVIDER
    authenticated = False

    def __init__(self, client: Optional[KalshiPublicHttpClient] = None, *,
                 quote_max_age_seconds=5.0, initialization_attempts=2,
                 initialization_backoff_seconds=0.25,
                 now: Optional[Callable[[], datetime]] = None):
        self.client = client or KalshiPublicHttpClient()
        self.quote_max_age_seconds = float(quote_max_age_seconds)
        if self.quote_max_age_seconds <= 0:
            raise ValueError("quote_max_age_seconds must be positive")
        self.now = now or (lambda: datetime.now(UTC))
        self.initialization_attempts = int(initialization_attempts)
        self.initialization_backoff_seconds = float(initialization_backoff_seconds)
        if not 1 <= self.initialization_attempts <= 3 or self.initialization_backoff_seconds < 0:
            raise ValueError("invalid mapping retry settings")
        self._mapping_cache: dict[tuple[str, datetime], KalshiMarketMapping] = {}
        self._series_cache: dict[str, dict] = {}

    def invalidate(self, asset: Optional[str] = None):
        if asset is None:
            self._mapping_cache.clear()
        else:
            for key in [key for key in self._mapping_cache if key[0] == asset]:
                self._mapping_cache.pop(key, None)

    def _series(self, ticker: str) -> dict:
        if ticker not in self._series_cache:
            payload = self.client.get("/series/" + ticker)
            row = payload.get("series")
            if not isinstance(row, dict):
                raise KalshiMalformedResponse("series object missing")
            self._series_cache[ticker] = row
        return self._series_cache[ticker]

    def discover(self, asset: str, window_start_utc: datetime, window_end_utc: datetime,
                 now: Optional[datetime] = None) -> KalshiQuoteResult:
        if asset not in SERIES_BY_ASSET:
            return KalshiQuoteResult(KalshiStatus.UNSUPPORTED_ASSET, detail=asset)
        start, end = _utc(window_start_utc), _utc(window_end_utc)
        observed = _utc(now or self.now())
        if (end - start).total_seconds() != 900 or start.minute % 15 or end.minute % 15 or start.second or end.second:
            return KalshiQuoteResult(KalshiStatus.MAPPING_INVALID, detail="window is not an exact UTC quarter-hour")
        key = (asset, end)
        cached = self._mapping_cache.get(key)
        if cached is not None and observed < cached.close_time_utc:
            return KalshiQuoteResult(KalshiStatus.READY, mapping=cached)
        self.invalidate(asset)
        series_ticker = SERIES_BY_ASSET[asset]
        try:
            series = self._series(series_ticker)
            if series.get("ticker") != series_ticker or series.get("frequency") != "fifteen_min":
                return KalshiQuoteResult(KalshiStatus.MAPPING_INVALID, detail="series metadata mismatch")
            matches = []
            for mapping_attempt in range(self.initialization_attempts):
                payload = self.client.get("/markets", series_ticker=series_ticker, limit=1000)
                rows = payload.get("markets")
                if not isinstance(rows, list):
                    raise KalshiMalformedResponse("markets list missing")
                matches = [row for row in rows if row.get("event_ticker") and
                           _parse_time(row.get("open_time")) == start and _parse_time(row.get("close_time")) == end]
                initializing = len(matches) == 1 and str(matches[0].get("status") or "").lower() in {"initialized", "unopened"}
                if initializing and mapping_attempt + 1 < self.initialization_attempts:
                    self.client.sleep(self.initialization_backoff_seconds * (2 ** mapping_attempt))
                    continue
                break
            if len(matches) > 1:
                return KalshiQuoteResult(KalshiStatus.MAPPING_AMBIGUOUS, detail=f"{len(matches)} exact-window markets")
            if not matches:
                return KalshiQuoteResult(KalshiStatus.MARKET_UNAVAILABLE, detail="no exact-window market")
            candidate = matches[0]
            status = str(candidate.get("status") or "").lower()
            if status in {"initialized", "unopened"}:
                return KalshiQuoteResult(KalshiStatus.MARKET_INITIALIZING, detail=candidate.get("ticker", ""))
            if status != "active" or observed >= end:
                return KalshiQuoteResult(KalshiStatus.MARKET_UNAVAILABLE, detail=f"market status {status or 'missing'}")
            market_payload = self.client.get("/markets/" + str(candidate["ticker"]))
            market = market_payload.get("market")
            if not isinstance(market, dict):
                raise KalshiMalformedResponse("market object missing")
            event_payload = self.client.get("/events/" + str(market.get("event_ticker")))
            event = event_payload.get("event")
            if not isinstance(event, dict):
                raise KalshiMalformedResponse("event object missing")
            sources = event.get("settlement_sources") or series.get("settlement_sources") or []
            source_names = {str(item.get("name", "")).strip().lower() for item in sources if isinstance(item, dict)}
            rules = str(market.get("rules_primary") or "") + " " + str(market.get("rules_secondary") or "")
            target = _decimal(market.get("floor_strike"), "target")
            verified = (market.get("ticker") == candidate.get("ticker") and
                        event.get("series_ticker") == series_ticker and
                        _parse_time(market.get("open_time")) == start and
                        _parse_time(market.get("close_time")) == end and
                        str(market.get("status", "")).lower() == "active" and
                        market.get("strike_type") == "greater_or_equal" and target > 0 and
                        "cf benchmarks" in source_names and "CF Benchmarks" in rules)
            if not verified:
                return KalshiQuoteResult(KalshiStatus.MAPPING_INVALID, detail="market/event provenance validation failed")
            mapping = KalshiMarketMapping(asset, series_ticker, str(event["event_ticker"]), str(market["ticker"]),
                str(market.get("title") or ""), str(market["status"]), start, end, end,
                _parse_time(market.get("expected_expiration_time")), str(market["strike_type"]), target,
                KALSHI_SETTLEMENT_SOURCE, KALSHI_SETTLEMENT_PROVENANCE,
                str(market.get("price_level_structure") or ""), True, observed)
            self._mapping_cache[key] = mapping
            return KalshiQuoteResult(KalshiStatus.READY, mapping=mapping)
        except KalshiError as exc:
            return KalshiQuoteResult(exc.status, detail=str(exc))

    def get_quote(self, asset: str, window_start_utc: datetime, window_end_utc: datetime,
                  now: Optional[datetime] = None) -> KalshiQuoteResult:
        observed = _utc(now or self.now())
        mapped = self.discover(asset, window_start_utc, window_end_utc, observed)
        if mapped.status is not KalshiStatus.READY or mapped.mapping is None:
            return mapped
        mapping = mapped.mapping
        try:
            # A cached mapping avoids repeating series/market discovery, but a
            # cheap single-market read prevents an early-inactivated contract
            # from lending its stale identity to a fresh orderbook request.
            current_payload = self.client.get("/markets/" + mapping.market_ticker)
            current_market = current_payload.get("market")
            if not isinstance(current_market, dict):
                raise KalshiMalformedResponse("market status object missing")
            current_status = str(current_market.get("status") or "").lower()
            if current_status != "active" or _parse_time(current_market.get("close_time")) != mapping.window_end_utc:
                self.invalidate(asset)
                status = KalshiStatus.MARKET_INITIALIZING if current_status in {"initialized", "unopened"} else KalshiStatus.MARKET_UNAVAILABLE
                return KalshiQuoteResult(status, mapping=mapping, detail=f"market status {current_status or 'missing'}")
            book = self.client.get("/markets/" + mapping.market_ticker + "/orderbook", depth=1)
            received = _utc(self.now())
            levels = parse_orderbook(book)
            # The public orderbook currently publishes no exchange timestamp.
            # received_at is therefore the honest quote timestamp and is labelled.
            quote = KalshiEventQuote(asset, mapping.market_ticker, mapping.event_ticker, mapping.series_ticker,
                levels["yes_bid"], levels["yes_bid_size"], levels["no_bid"], levels["no_bid_size"],
                levels["yes_ask"], levels["yes_ask_size"], levels["no_ask"], levels["no_ask_size"],
                received, "LOCAL_RECEIVED_AT_NO_EXCHANGE_TIMESTAMP", received, Decimal("0"), mapping.status,
                KALSHI_PROVIDER, False, mapping.mapping_verified, True, mapping.settlement_source,
                mapping.settlement_reference_provenance, mapping.target, mapping.window_start_utc, mapping.window_end_utc)
            quote = replace(quote, quote_age_seconds=Decimal(str(max(0.0, (_utc(self.now()) - received).total_seconds()))))
            if not quote.fresh_at(self.now(), self.quote_max_age_seconds):
                return KalshiQuoteResult(KalshiStatus.QUOTE_STALE, mapping=mapping, quote=replace(quote, quote_verified=False))
            if quote.yes_ask is None or quote.no_ask is None:
                return KalshiQuoteResult(KalshiStatus.BOOK_SIDE_MISSING, mapping=mapping,
                                         quote=replace(quote, quote_verified=False), detail="one complementary ask is unavailable")
            return KalshiQuoteResult(KalshiStatus.READY, mapping=mapping, quote=quote)
        except KalshiError as exc:
            self.invalidate(asset)
            return KalshiQuoteResult(exc.status, mapping=mapping, detail=str(exc))


def _money(value):
    return "UNAVAILABLE" if value is None else f"${value}"


def render_kalshi_check(provider: Optional[KalshiEventMarketProvider] = None,
                        now: Optional[datetime] = None) -> tuple[str, bool]:
    provider = provider or KalshiEventMarketProvider()
    now = _utc(now or datetime.now(UTC))
    minute = (now.minute // 15) * 15
    start = now.replace(minute=minute, second=0, microsecond=0)
    end = start + timedelta(minutes=15)
    lines = ["KALSHI PUBLIC EVENT DATA", "", "CONNECTIVITY ............ CHECKING", ""]
    ready = True
    for asset in SERIES_BY_ASSET:
        result = provider.get_quote(asset, start, end, now)
        label = asset.replace("-USD", "")
        lines.append(label)
        if result.status is not KalshiStatus.READY or result.quote is None:
            lines.append(f"STATUS .................. {result.status.value}")
            if result.detail: lines.append(f"DETAIL .................. {result.detail}")
            lines.append("")
            ready = ready and result.status is KalshiStatus.MARKET_INITIALIZING
            continue
        quote, mapping = result.quote, result.mapping
        lines.extend((f"MARKET .................. {mapping.market_ticker}",
            f"WINDOW UTC .............. {mapping.window_start_utc:%H:%M} -> {mapping.window_end_utc:%H:%M}",
            f"TARGET .................. {_money(mapping.target)}",
            f"YES BID ................. {_money(quote.yes_bid)}",
            f"YES ASK ................. {_money(quote.yes_ask)}",
            f"YES ASK SIZE ............ {quote.yes_ask_size if quote.yes_ask_size is not None else 'UNAVAILABLE'}",
            f"NO BID .................. {_money(quote.no_bid)}",
            f"NO ASK .................. {_money(quote.no_ask)}",
            f"NO ASK SIZE ............. {quote.no_ask_size if quote.no_ask_size is not None else 'UNAVAILABLE'}",
            f"STATUS .................. {mapping.status.upper()}", "MAPPING ................. VERIFIED", ""))
    lines[2] = "CONNECTIVITY ............ READY" if ready else "CONNECTIVITY ............ DEGRADED"
    lines.append("RESULT: READY" if ready else "RESULT: DEGRADED")
    return "\n".join(lines), ready
