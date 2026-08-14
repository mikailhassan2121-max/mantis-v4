# Phase 7 Decision Integration

> **PROXY SETTLEMENT REFERENCE**  
> **NO HISTORICAL WEBULL CONTRACT QUOTES**  
> **CLASSIFICATION RESEARCH ONLY**  
> **NOT A PROFITABILITY BACKTEST**  
> **LIMITED RECENT HISTORICAL REGIME**

```text
data quality
  -> locked Phase 6 classification policy
  -> verified economics provider available
  -> executable quote identity/freshness/book checks
  -> point EV positive
  -> lower-bound EV positive
  -> model-edge, LCB-edge, return-on-cost gates
  -> ENTER YES / ENTER NO (hold to resolution)
```

Classification failures retain their original Phase 6 decision and reason. Missing economics returns `WAIT — CONTRACT QUOTE UNAVAILABLE`; stale or invalid quotes return `DATA HOLD`; unverified economics or negative EV returns `NO TRADE`; non-robust or insufficient-margin EV returns `WAIT`.

`Phase7Config` exposes every classification precondition and economic gate. Defaults preserve the accepted Phase 6 rule. The economic defaults are architecture/research settings only and are not production-validated.

The structured assessment exposes Phase 9 display fields: side, point probability, lower bound, reference/source/verification, expiration, YES and NO bid/ask, quote age, break-even probability, point and LCB edge, gross/net point and LCB EV, expected return on cost, maximum loss/payout, fee/slippage status, economic status, decision, and reason.

Provider priority is official Webull, verified manual JSON, then underlying proxy. Proxy always returns no economics and therefore displays:

```text
REFERENCE: PROXY / UNVERIFIED
CONTRACT ECONOMICS: UNAVAILABLE
EV ENGINE: DISABLED
```
