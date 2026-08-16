#!/usr/bin/env python3
"""Anonymous, read-only Kalshi 15-minute crypto market probe.

Uses only documented public GET endpoints. It never authenticates, submits an
order, imports MANTIS runtime code, or writes forward-validation data.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from decimal import Decimal
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

BASE = "https://external-api.kalshi.com/trade-api/v2"
ASSET_TERMS = {
    "BTC": ("btc", "bitcoin"),
    "ETH": ("eth", "ethereum"),
    "SOL": ("sol", "solana"),
    "XRP": ("xrp",),
}
UTC = timezone.utc
EASTERN = ZoneInfo("America/New_York")


class PublicClient:
    def __init__(self, timeout: float = 20.0):
        self.timeout = timeout
        self.calls: list[dict] = []

    def get(self, path: str, **params):
        query = "?" + urlencode(params) if params else ""
        url = BASE + path + query
        request = Request(url, headers={"Accept": "application/json", "User-Agent": "MANTIS-Kalshi-Probe/1.0"})
        for attempt in range(3):
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    body = json.load(response)
                    self.calls.append({"endpoint": path, "status": response.status,
                                       "top_level_keys": sorted(body)})
                    return body
            except HTTPError as exc:
                if exc.code == 429 and attempt < 2:
                    time.sleep(0.5 * (2 ** attempt))
                    continue
                raise RuntimeError(f"GET {path} returned HTTP {exc.code}") from exc
            except URLError as exc:
                raise RuntimeError(f"GET {path} failed: {exc.reason}") from exc
        raise RuntimeError(f"GET {path} exhausted retries")


def parse_time(value):
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)


def formatted_time(value):
    instant = parse_time(value)
    if instant is None:
        return {"utc": None, "eastern": None}
    return {"utc": instant.isoformat(), "eastern": instant.astimezone(EASTERN).isoformat()}


def discover_series(series_rows):
    found = {}
    fifteen = [row for row in series_rows if row.get("frequency") == "fifteen_min"]
    for asset, terms in ASSET_TERMS.items():
        exact = [row for row in fifteen if asset in {str(tag).upper() for tag in (row.get("tags") or [])}]
        if len(exact) == 1:
            found[asset] = exact[0]
            continue
        matches = []
        for row in fifteen:
            text = " ".join((str(row.get("ticker", "")), str(row.get("title", "")),
                             " ".join(row.get("tags") or []))).lower()
            if any(term in text.split() or term in str(row.get("title", "")).lower() for term in terms):
                matches.append(row)
        if len(matches) != 1:
            raise RuntimeError(f"ambiguous {asset} fifteen-minute series: {[m.get('ticker') for m in matches]}")
        found[asset] = matches[0]
    return found


def current_market(markets, now):
    live = [m for m in markets if parse_time(m.get("open_time")) <= now < parse_time(m.get("close_time"))]
    if live:
        return min(live, key=lambda m: parse_time(m.get("close_time")))
    future = [m for m in markets if parse_time(m.get("close_time")) and parse_time(m.get("close_time")) > now]
    return min(future, key=lambda m: parse_time(m.get("close_time"))) if future else None


def top_of_book(payload):
    book = payload.get("orderbook_fp") or {}
    yes = [(Decimal(price), Decimal(quantity)) for price, quantity in book.get("yes_dollars", [])]
    no = [(Decimal(price), Decimal(quantity)) for price, quantity in book.get("no_dollars", [])]
    best_yes = max(yes, default=None, key=lambda level: level[0])
    best_no = max(no, default=None, key=lambda level: level[0])
    one = Decimal("1.0000")
    return {
        "response_object": "orderbook_fp",
        "price_field": "yes_dollars/no_dollars level[0] (fixed-point dollar string)",
        "quantity_field": "yes_dollars/no_dollars level[1] (fixed-point count string)",
        "best_yes_bid_dollars": str(best_yes[0]) if best_yes else None,
        "best_yes_bid_quantity_fp": str(best_yes[1]) if best_yes else None,
        "best_no_bid_dollars": str(best_no[0]) if best_no else None,
        "best_no_bid_quantity_fp": str(best_no[1]) if best_no else None,
        "derived_yes_ask_dollars": str(one - best_no[0]) if best_no else None,
        "derived_yes_ask_quantity_fp": str(best_no[1]) if best_no else None,
        "derived_no_ask_dollars": str(one - best_yes[0]) if best_yes else None,
        "derived_no_ask_quantity_fp": str(best_yes[1]) if best_yes else None,
    }


def probe(timeout=20.0):
    client = PublicClient(timeout)
    # Explicit connectivity checks requested for each public collection.
    client.get("/markets", limit=1, status="open")
    client.get("/events", limit=1, status="open")
    series_payload = client.get("/series", category="Crypto")
    series = discover_series(series_payload.get("series", []))
    now = datetime.now(UTC)
    results = {}
    for asset, series_row in series.items():
        ticker = series_row["ticker"]
        markets = client.get("/markets", series_ticker=ticker, status="open", limit=100).get("markets", [])
        market = current_market(markets, now)
        if market is None:
            results[asset] = {"series_ticker": ticker, "error": "NO CURRENT OR FUTURE OPEN MARKET"}
            continue
        event = client.get("/events/" + market["event_ticker"]).get("event", {})
        detail = client.get("/markets/" + market["ticker"]).get("market", market)
        orderbook = client.get("/markets/" + market["ticker"] + "/orderbook", depth=1)
        open_at, close_at = parse_time(detail.get("open_time")), parse_time(detail.get("close_time"))
        results[asset] = {
            "market_ticker": detail.get("ticker"),
            "event_ticker": detail.get("event_ticker"),
            "series_ticker": ticker,
            "series_title": series_row.get("title"),
            "series_frequency": series_row.get("frequency"),
            "title": detail.get("title"),
            "subtitle": detail.get("subtitle") or detail.get("yes_sub_title"),
            "event_title": event.get("title"),
            "event_subtitle": event.get("sub_title"),
            "status": detail.get("status"),
            "open_time": formatted_time(detail.get("open_time")),
            "close_time": formatted_time(detail.get("close_time")),
            "expiration_time": formatted_time(detail.get("expiration_time")),
            "expected_expiration_time": formatted_time(detail.get("expected_expiration_time")),
            "latest_expiration_time": formatted_time(detail.get("latest_expiration_time")),
            "duration_seconds": int((close_at - open_at).total_seconds()) if open_at and close_at else None,
            "quarter_hour_aligned": bool(open_at and close_at and open_at.minute % 15 == 0 and
                                         close_at.minute % 15 == 0 and open_at.second == close_at.second == 0 and
                                         (close_at - open_at).total_seconds() == 900),
            "strike_type": detail.get("strike_type"),
            "floor_strike": detail.get("floor_strike"),
            "cap_strike": detail.get("cap_strike"),
            "functional_strike": detail.get("functional_strike"),
            "custom_strike": detail.get("custom_strike"),
            "rules_primary": detail.get("rules_primary"),
            "rules_secondary": detail.get("rules_secondary"),
            "settlement_timer_seconds": detail.get("settlement_timer_seconds"),
            "price_level_structure": detail.get("price_level_structure"),
            "settlement_sources": event.get("settlement_sources") or series_row.get("settlement_sources"),
            "contract_terms_url": series_row.get("contract_terms_url"),
            "orderbook": top_of_book(orderbook),
        }
    return {"observed_at_utc": now.isoformat(), "anonymous_connectivity": client.calls,
            "markets": results}


def main():
    parser = argparse.ArgumentParser(description="Read-only public Kalshi 15-minute crypto probe")
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()
    print(json.dumps(probe(args.timeout), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
