# Phase 8 Failure and Recovery

> **PROXY SETTLEMENT REFERENCE**  
> **NO HISTORICAL WEBULL CONTRACT QUOTES**  
> **CLASSIFICATION RESEARCH ONLY**  
> **NOT A PROFITABILITY BACKTEST**  
> **LIMITED RECENT HISTORICAL REGIME**  
> **WEBULL AUTH NOT CONFIGURED**  
> **ECONOMIC THRESHOLDS NOT HISTORICALLY VALIDATED**

Each JSONL append is flushed and fsynced. Restart rebuilds all IDs, entry deduplication, unresolved-entry work, and current-window crossing state from disk. A truncated final line is ignored as an interrupted append; malformed earlier lines fail loudly rather than silently dropping history.

Safe states:

- stale or unavailable underlying → `DATA HOLD`
- Webull unavailable → classification-only proxy-safe operation
- quote unavailable → `WAIT — CONTRACT QUOTE UNAVAILABLE`
- stale/invalid/mismatched quote → `DATA HOLD`
- proxy reference → economics disabled unless a separately verified matching manual/official contract is supplied
- malformed configuration → startup failure with an actionable exception

No missing numeric input is converted to zero and no stale economics is silently reused.
