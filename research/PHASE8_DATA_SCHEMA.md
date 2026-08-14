# Phase 8 Forward Data Schema

> **PROXY SETTLEMENT REFERENCE**  
> **NO HISTORICAL WEBULL CONTRACT QUOTES**  
> **CLASSIFICATION RESEARCH ONLY**  
> **NOT A PROFITABILITY BACKTEST**  
> **LIMITED RECENT HISTORICAL REGIME**  
> **WEBULL AUTH NOT CONFIGURED**  
> **ECONOMIC THRESHOLDS NOT HISTORICALLY VALIDATED**

Raw append-only files live under `data/forward/` and are ignored by Git:

- `runs.jsonl`: run ID, timestamp, software/model versions, git commit, config/model hashes, provider modes.
- `observations.jsonl`: causal scan state, classification, economics, decision, `OUTCOME_STATUS=PENDING`.
- `entries.jsonl`: first qualifying Phase 6 entry event for an asset/contract.
- `resolutions.jsonl`: terminal value and independently linked outcome.
- `provider_health.jsonl`: provider successes, failures, state, latency-related context.

Observation rows are never rewritten. Resolution and derived CSV exports are separate. `export_csv` deterministically exports JSONL sources without overwriting them.

The observation schema includes UTC/local timestamps, window identity, reference/status, current price/buffer/volatility, point and conservative probabilities, fragility/disagreement/crossing diagnostics, Phase 6 state, executable contract book when verified, quote freshness, break-even/edge/EV fields, fee/slippage status, final advisory, user action, and reproducibility hashes.
