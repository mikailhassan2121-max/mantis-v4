# Phase 8 Forward Validation

> **PROXY SETTLEMENT REFERENCE**  
> **NO HISTORICAL WEBULL CONTRACT QUOTES**  
> **CLASSIFICATION RESEARCH ONLY**  
> **NOT A PROFITABILITY BACKTEST**  
> **LIMITED RECENT HISTORICAL REGIME**  
> **WEBULL AUTH NOT CONFIGURED**  
> **ECONOMIC THRESHOLDS NOT HISTORICALLY VALIDATED**

Every scan is an immutable observation written before resolution. The first Phase 6 ENTER per asset/contract becomes one entry event even when Phase 7 must wait for economics. Later scans cannot create duplicate trades. `USER_ACTION` remains `NONE` unless explicitly supplied and never changes model output.

Resolution is a separate append. Proxy underlying resolution is labeled `PROXY_RESOLUTION`; it is never called official Webull settlement. Classification correctness and economic-result verification are separate fields.

Forward reports operate on entry/resolution joins, never on correlated five-second observations. They report contracts, entries, abstention, grouped confidence intervals, YES/NO and per-asset accuracy, and entry-time distribution. Sample labels are: under 30 very small, 30–99 small, 100–299 early evidence, 300–999 meaningful forward sample, and 1000+ larger forward sample.

Reliability output separately totals provider successes/failures, stale observations, data holds, and out-of-order records. Raw provider-health events retain provider state and diagnostic details so missing/duplicate bar incidents remain auditable rather than being converted to predictions.

Historical comparison is pinned to 96.58% accuracy and 63.73% coverage. It emits `FORWARD_SAMPLE_TOO_SMALL`, `FORWARD_REGIME_NORMAL`, or `FORWARD_REGIME_SHIFT_WARNING`; it never retunes thresholds.

`ForwardReplayHarness` accepts only chronological precomputed live states and exercises the same logging, rollover, entry-deduplication, and transition mechanics. It exposes no threshold-selection or model-fitting method and is not a Phase 6 accuracy backtest.
