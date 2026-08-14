# Phase 7 Webull Provider Research

> **PROXY SETTLEMENT REFERENCE**  
> **NO HISTORICAL WEBULL CONTRACT QUOTES**  
> **CLASSIFICATION RESEARCH ONLY**  
> **NOT A PROFITABILITY BACKTEST**  
> **LIMITED RECENT HISTORICAL REGIME**

Research date: 2026-08-14. Only official Webull documentation was used.

## Confirmed official capabilities

- Event contracts are supported and have official instrument discovery endpoints for categories, series, events, and markets.
- Event snapshot returns documented `yes_bid`, `yes_ask`, `no_bid`, `no_ask`, side sizes, last YES trade price/time, volume, open interest, symbol, and instrument ID.
- Event depth returns YES and NO bids; the documentation explains the complementary bid/ask relationship rather than returning asks in that endpoint.
- Event bars, ticks, and MQTT event quote/snapshot/tick streaming are documented.
- HTTP event snapshot/depth/bars/ticks are documented at 600 requests/minute in the current individual OpenAPI reference; instrument discovery is documented at 60 requests/60 seconds. The general Data API overview gives 300 requests/60 seconds, so implementations must honor the most restrictive endpoint-specific limit and server responses.
- Requests require an App Key and App Secret; the SDK performs signing. Authenticated access/token permissions are required.
- The current market-data overview says US event contracts require no additional market-data subscription. Other products may require separate non-display OpenAPI subscriptions, and app/desktop subscriptions do not transfer.
- Winning event shares pay $1 and losing shares pay $0. Official trading documentation currently states LIMIT/DAY orders and a $0.01 exchange plus $0.01 firm fee per contract on opening and closing trades.

Sources: [Market Data overview](https://developer.webull.com/apis/docs/market-data-api/overview/), [Event snapshot](https://developer.webull.com/apis/docs/reference/event-snapshot/), [Event market data](https://developer.webull.com/apis/docs/reference/event-market-data/), [streaming schema](https://developer.webull.com/apis/docs/market-data-api/data-streaming-api/), [event trading](https://developer.webull.com/apis/docs/trade-api/event-contract/), [authentication FAQ](https://developer.webull.com/apis/docs/market-data-api/faq/).

## Deliberately not assumed

- No private/mobile/desktop endpoint or client spoofing is used.
- A generic event snapshot does not by itself prove that a returned instrument matches MANTIS’s active 15-minute crypto contract, official reference, resolution, or settlement rule.
- The documented fee schedule is not silently installed as a live account fee. Until the active contract/account fee basis is verified, `FEES_STATUS = UNKNOWN_FEES` and only `EV_BEFORE_UNVERIFIED_FEES` is shown.
- Snapshot `last_trade_time` is a trade timestamp, not necessarily quote-generation time. A live adapter must obtain a defensible receipt/source timestamp for freshness; it must not relabel last trade time as quote time.

Current local state has no configured credentials, so `WEBULL_STATUS = AUTH_NOT_CONFIGURED`. The official provider returns no economics and fallback continues cleanly.
