# Saaf Ventures Intelligence architecture

## Scope

SVI 1.0 is a manual-only market-research platform embedded beside MANTIS. It
normalizes already-collected observations, runs isolated probability specialists,
records immutable evidence, and evaluates forecasts after verified resolution.
It does not authenticate to a brokerage, size positions, submit orders, tune
policies automatically, or calculate trading P&L.

## Data flow

1. MANTIS performs its authoritative scan using its existing policies and memory.
2. `SVI_DATA_NORMALIZATION_V1` adds immutable identity, reference, quote-quality,
   and provenance fields without changing the MANTIS selection dictionary.
3. The supervisor runs registered agents in isolation. MANTIS is `ADVISORY`, the
   Kalshi midpoint is `BENCHMARK`, and new challengers begin as `SHADOW`.
4. Only advisory candidates may enter review ranking and non-actionable consensus.
5. Schema-6 events are appended to the SVI JSONL ledger and flushed to disk.
6. Only forecasts made before an official `VERIFIED` resolution are scored.
7. Calibration, market-relative skill, cross-asset robustness, chronological
   replay, attribution, temporal drift, and lifecycle state are reported read-only.

## Safety boundaries

- The only execution mode is `MANUAL_ONLY`.
- Benchmark and shadow records must be unranked.
- Consensus is always diagnostic and non-actionable.
- Promotion and demotion require explicit reviewed configuration changes.
- Admission requires sample size, paired lower confidence bounds, recent
  stability, multi-asset depth, worst-asset robustness, complementarity, and
  passing drift status.
- Historical replay never fits a model, tunes a policy, simulates trades, or
  calculates P&L.

The machine-readable source of truth is `platform_manifest()` in
`saaf_ventures_intelligence/platform.py`.
