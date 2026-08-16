import json
import socket
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from mantis_v4.economics.kalshi import (
    KALSHI_PROVIDER, KALSHI_SETTLEMENT_PROVENANCE, KALSHI_SETTLEMENT_SOURCE,
    SERIES_BY_ASSET, KalshiEventMarketProvider, KalshiHTTPError,
    KalshiMalformedResponse, KalshiPublicHttpClient, KalshiStatus, KalshiTimeout,
    parse_orderbook,
)

UTC=timezone.utc
START=datetime(2026,8,15,20,15,tzinfo=UTC)
END=START+timedelta(minutes=15)
FIXTURES=Path(__file__).parent/"fixtures"/"kalshi"

def fixture(name): return json.loads((FIXTURES/name).read_text(encoding="utf-8"))

def series_payload(ticker):
    asset=next(asset for asset,series in SERIES_BY_ASSET.items() if series==ticker)
    return {"series":{"ticker":ticker,"frequency":"fifteen_min","title":asset,
      "tags":[asset.split("-")[0],"15 min"],"settlement_sources":[{"name":"CF Benchmarks"}]}}

def market_row(ticker,status="active",start=START,end=END,event=None):
    return {"ticker":ticker+"-WINDOW","event_ticker":event or ticker+"-EVENT","status":status,
      "open_time":start.isoformat(),"close_time":end.isoformat()}

def market_payload(ticker,status="active",start=START,end=END,event=None,series=None):
    event=event or ticker+"-EVENT"
    return {"market":{"ticker":ticker+"-WINDOW","event_ticker":event,"title":"15 minute market",
      "status":status,"open_time":start.isoformat(),"close_time":end.isoformat(),
      "expected_expiration_time":(end+timedelta(minutes=5)).isoformat(),
      "expiration_time":(end+timedelta(days=7)).isoformat(),"strike_type":"greater_or_equal",
      "floor_strike":"123.4567","price_level_structure":"tapered_deci_cent",
      "rules_primary":"CF Benchmarks 60-second endpoint average.",
      "rules_secondary":"CF Benchmarks Real Time Index."}}

def event_payload(ticker,series=None):
    return {"event":{"event_ticker":ticker+"-EVENT","series_ticker":series or ticker,
      "settlement_sources":[{"name":"CF Benchmarks"}]}}

class FakeClient:
    def __init__(self,book=None):
        self.book=book or fixture("orderbook_two_sided.json"); self.calls=[]; self.sleeps=[]
        self.market_status="active"; self.market_rows=None; self.event_series=None
    def sleep(self,value): self.sleeps.append(value)
    def get(self,path,**params):
        self.calls.append((path,params))
        if path.startswith("/series/"): return series_payload(path.rsplit("/",1)[-1])
        if path=="/markets":
            ticker=params["series_ticker"]
            return {"markets":self.market_rows if self.market_rows is not None else [market_row(ticker,self.market_status)]}
        if path.startswith("/events/"):
            ticker=path.rsplit("/",1)[-1].removesuffix("-EVENT")
            return event_payload(ticker,self.event_series)
        if path.endswith("/orderbook"): return self.book
        if path.startswith("/markets/"):
            ticker=path.rsplit("/",1)[-1].removesuffix("-WINDOW")
            return market_payload(ticker,self.market_status)
        raise AssertionError(path)

class KalshiMappingTests(unittest.TestCase):
    def provider(self,client=None,**kwargs):
        return KalshiEventMarketProvider(client or FakeClient(),now=lambda:START+timedelta(minutes=5),
          initialization_backoff_seconds=0,**kwargs)

    def test_exact_supported_series_and_no_ada(self):
        self.assertEqual(SERIES_BY_ASSET,{"BTC-USD":"KXBTC15M","ETH-USD":"KXETH15M",
          "SOL-USD":"KXSOL15M","XRP-USD":"KXXRP15M"})
        self.assertNotIn("ADA-USD",SERIES_BY_ASSET)

    def test_each_asset_maps_to_exact_series(self):
        for asset,series in SERIES_BY_ASSET.items():
            with self.subTest(asset=asset):
                result=self.provider().discover(asset,START,END)
                self.assertEqual(result.status,KalshiStatus.READY)
                self.assertEqual(result.mapping.series_ticker,series)

    def test_exact_window_uses_close_not_later_expiration(self):
        mapping=self.provider().discover("BTC-USD",START,END).mapping
        self.assertEqual(mapping.close_time_utc,END)
        self.assertEqual(mapping.expected_expiration_time_utc,END+timedelta(minutes=5))

    def test_wrong_window_and_expired_previous_rejected(self):
        client=FakeClient(); client.market_rows=[market_row("KXBTC15M",start=START-timedelta(minutes=15),end=START)]
        self.assertEqual(self.provider(client).discover("BTC-USD",START,END).status,KalshiStatus.MARKET_UNAVAILABLE)
        provider=KalshiEventMarketProvider(FakeClient(),now=lambda:END,initialization_backoff_seconds=0)
        self.assertEqual(provider.discover("BTC-USD",START,END).status,KalshiStatus.MARKET_UNAVAILABLE)

    def test_bad_window_and_unsupported_asset_fail_closed(self):
        provider=self.provider()
        self.assertEqual(provider.discover("ADA-USD",START,END).status,KalshiStatus.UNSUPPORTED_ASSET)
        self.assertEqual(provider.discover("BTC-USD",START,END+timedelta(minutes=1)).status,KalshiStatus.MAPPING_INVALID)

    def test_initialized_market_retries_bounded_then_reports_initializing(self):
        client=FakeClient(); client.market_status="initialized"
        result=self.provider(client).discover("BTC-USD",START,END)
        self.assertEqual(result.status,KalshiStatus.MARKET_INITIALIZING)
        self.assertEqual(sum(path=="/markets" for path,_ in client.calls),2)
        self.assertEqual(len(client.sleeps),1)

    def test_initialized_market_can_transition_to_active_on_bounded_retry(self):
        class TransitionClient(FakeClient):
            def __init__(self): super().__init__(); self.lookups=0
            def get(self,path,**params):
                if path=="/markets":
                    self.calls.append((path,params)); self.lookups+=1
                    status="initialized" if self.lookups==1 else "active"
                    return {"markets":[market_row(params["series_ticker"],status)]}
                return super().get(path,**params)
        client=TransitionClient(); result=self.provider(client).discover("BTC-USD",START,END)
        self.assertEqual(result.status,KalshiStatus.READY)
        self.assertEqual(client.lookups,2)

    def test_ambiguous_market_rejected(self):
        client=FakeClient(); client.market_rows=[market_row("KXBTC15M"),market_row("KXBTC15M-ALT")]
        self.assertEqual(self.provider(client).discover("BTC-USD",START,END).status,KalshiStatus.MAPPING_AMBIGUOUS)

    def test_wrong_event_series_rejected(self):
        client=FakeClient(); client.event_series="KXETH15M"
        self.assertEqual(self.provider(client).discover("BTC-USD",START,END).status,KalshiStatus.MAPPING_INVALID)

    def test_mapping_cache_and_rollover_invalidation(self):
        client=FakeClient(); provider=self.provider(client)
        provider.discover("BTC-USD",START,END); provider.discover("BTC-USD",START,END)
        self.assertEqual(sum(path=="/markets" for path,_ in client.calls),1)
        client.market_rows=[market_row("KXBTC15M",start=END,end=END+timedelta(minutes=15))]
        provider.discover("BTC-USD",END,END+timedelta(minutes=15),END+timedelta(minutes=1))
        self.assertEqual(sum(path=="/markets" for path,_ in client.calls),2)

    def test_settlement_provenance_is_preserved(self):
        mapping=self.provider().discover("BTC-USD",START,END).mapping
        self.assertEqual((mapping.settlement_source,mapping.settlement_reference_provenance),
          (KALSHI_SETTLEMENT_SOURCE,KALSHI_SETTLEMENT_PROVENANCE))

class KalshiOrderbookTests(unittest.TestCase):
    def test_defensive_highest_bid_and_derived_asks_and_sizes(self):
        top=parse_orderbook(fixture("orderbook_two_sided.json"))
        self.assertEqual(top["yes_bid"],Decimal("0.4900")); self.assertEqual(top["no_bid"],Decimal("0.5000"))
        self.assertEqual(top["yes_ask"],Decimal("0.5000")); self.assertEqual(top["no_ask"],Decimal("0.5100"))
        self.assertEqual(top["yes_ask_size"],Decimal("1666.06")); self.assertEqual(top["no_ask_size"],Decimal("810.01"))

    def test_empty_sides_leave_complementary_ask_unavailable(self):
        empty_yes=parse_orderbook(fixture("orderbook_empty_yes.json"))
        empty_no=parse_orderbook(fixture("orderbook_empty_no.json"))
        self.assertIsNone(empty_yes["no_ask"]); self.assertEqual(empty_yes["yes_ask"],Decimal("0.3700"))
        self.assertIsNone(empty_no["yes_ask"]); self.assertEqual(empty_no["no_ask"],Decimal("0.6400"))

    def test_subcent_and_fractional_values_preserved(self):
        top=parse_orderbook(fixture("orderbook_subcent.json"))
        self.assertEqual(top["yes_bid"],Decimal("0.0145")); self.assertEqual(top["yes_bid_size"],Decimal("10.25"))
        self.assertEqual(top["yes_ask"],Decimal("0.0155")); self.assertEqual(top["yes_ask_size"],Decimal("4.75"))

    def test_malformed_prices_and_ranges_rejected(self):
        with self.assertRaises(KalshiMalformedResponse): parse_orderbook(fixture("malformed.json"))
        with self.assertRaises(KalshiMalformedResponse): parse_orderbook({"orderbook_fp":{"yes_dollars":[["1.1","2"]],"no_dollars":[]}})

    def test_quote_object_is_read_only_verified_and_fresh(self):
        provider=KalshiEventMarketProvider(FakeClient(),now=lambda:START+timedelta(minutes=5),initialization_backoff_seconds=0)
        result=provider.get_quote("BTC-USD",START,END)
        self.assertEqual(result.status,KalshiStatus.READY); quote=result.quote
        self.assertEqual((quote.provider,quote.authenticated,quote.mapping_verified,quote.quote_verified),(KALSHI_PROVIDER,False,True,True))
        self.assertEqual(quote.quote_timestamp_provenance,"LOCAL_RECEIVED_AT_NO_EXCHANGE_TIMESTAMP")
        self.assertTrue(quote.fresh_at(quote.received_at_utc+timedelta(seconds=5),5))
        self.assertFalse(quote.fresh_at(quote.received_at_utc+timedelta(seconds=6),5))
        with self.assertRaises(Exception): quote.yes_bid=Decimal("0.1")

    def test_one_sided_book_result_is_not_verified(self):
        provider=KalshiEventMarketProvider(FakeClient(fixture("orderbook_empty_yes.json")),now=lambda:START+timedelta(minutes=5),initialization_backoff_seconds=0)
        result=provider.get_quote("BTC-USD",START,END)
        self.assertEqual(result.status,KalshiStatus.BOOK_SIDE_MISSING)
        self.assertFalse(result.quote.quote_verified)

    def test_provider_marks_quote_stale_when_local_receipt_guard_expires(self):
        moments=iter([START+timedelta(minutes=5),START+timedelta(minutes=5),
          START+timedelta(minutes=5,seconds=6),START+timedelta(minutes=5,seconds=6)])
        provider=KalshiEventMarketProvider(FakeClient(),now=lambda:next(moments),quote_max_age_seconds=5,
          initialization_backoff_seconds=0)
        result=provider.get_quote("BTC-USD",START,END)
        self.assertEqual(result.status,KalshiStatus.QUOTE_STALE)
        self.assertFalse(result.quote.quote_verified)

class KalshiHttpTests(unittest.TestCase):
    def test_429_and_500_retry_with_bounded_exponential_backoff(self):
        for code in (429,500):
            calls=[]; sleeps=[]
            def transport(url,timeout):
                calls.append(url)
                if len(calls)<3: raise KalshiHTTPError(code)
                return 200,b'{"markets":[]}'
            client=KalshiPublicHttpClient(transport=transport,max_attempts=3,backoff_seconds=.25,sleep=sleeps.append)
            self.assertEqual(client.get("/markets"),{"markets":[]})
            self.assertEqual((len(calls),sleeps),(3,[.25,.5]))

    def test_timeout_retry_is_bounded(self):
        calls=[]
        def transport(url,timeout): calls.append(url); raise KalshiTimeout("timeout")
        client=KalshiPublicHttpClient(transport=transport,max_attempts=2,backoff_seconds=0,sleep=lambda _:None)
        with self.assertRaises(KalshiTimeout): client.get("/markets")
        self.assertEqual(len(calls),2)

    def test_malformed_json_is_not_retried(self):
        calls=[]
        def transport(url,timeout): calls.append(url); return 200,b'{bad'
        client=KalshiPublicHttpClient(transport=transport,max_attempts=3,sleep=lambda _:None)
        with self.assertRaises(KalshiMalformedResponse): client.get("/markets")
        self.assertEqual(len(calls),1)

    def test_portfolio_and_order_paths_are_impossible(self):
        client=KalshiPublicHttpClient(transport=lambda *_: {})
        for path in ("/portfolio/orders","/markets/X/order"):
            with self.assertRaises(ValueError): client.get(path)

    def test_source_has_no_auth_trading_or_forward_store_dependency(self):
        source=(Path(__file__).parents[1]/"mantis_v4/economics/kalshi.py").read_text(encoding="utf-8")
        for forbidden in ("KALSHI-ACCESS-KEY","ForwardStore","data/forward","requests.post","urlopen(request, data="):
            self.assertNotIn(forbidden,source)

if __name__=="__main__": unittest.main()
