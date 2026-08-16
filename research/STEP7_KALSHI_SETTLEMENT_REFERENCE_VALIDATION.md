# Step 7 — Kalshi settlement/reference validation

Status: `RESEARCH_SHADOW_ONLY`
Specifications: `KALSHI_SETTLEMENT_EVENT_V1`, `CF_REFERENCE_VALIDATION_V1`,
`ENDING_60S_RECONSTRUCTION_V1`

## Known official semantics

Kalshi's official crypto-market guidance states that crypto contracts use CF
Benchmarks Real-Time Indices, published about once per second, and that the
official expiration value is the simple average of the sixty RTI values in the
last minute before expiration. Current public market rules confirm `>=` settles
YES, use `[expiration-60s, expiration)` language, and round the resulting value
to two decimals for BTC/ETH and four decimals for SOL/XRP.

Official benchmark mapping:

| Asset | Kalshi series | CF product |
|---|---|---|
| BTC | KXBTC15M | BRTI |
| ETH | KXETH15M | ETHUSD_RTI |
| SOL | KXSOL15M | SOLUSD_RTI |
| XRP | KXXRP15M | XRPUSD_RTI |

Sources:

- https://help.kalshi.com/en/articles/13823838-crypto-markets
- https://external-api.kalshi.com/trade-api/v2
- https://assets.kalshi.com/contract_terms/CRYPTO15M.pdf
- https://docs.cfbenchmarks.com/CME%20CF%20Real%20Indices%20Methodology.pdf
- https://www.cfbenchmarks.com/data/indices/BRTI
- https://www.cfbenchmarks.com/data/indices/ETHUSD_RTI
- https://www.cfbenchmarks.com/data/indices/SOLUSD_RTI
- https://www.cfbenchmarks.com/data/indices/XRPUSD_RTI

The exact per-market `rules_primary` remains authoritative. Some rules express
the comparison directly against the starting 60-second average; others express
the already-published numeric `floor_strike`. Consequently the machine-readable
specification is `PARTIALLY_VERIFIED`, not overclaimed as fully verified.

## Direct CF access

`DIRECT_CF_DATA_STATUS = UNAVAILABLE`

The anonymous official index directory returned an empty payload and anonymous
value requests for BRTI, ETHUSD_RTI, SOLUSD_RTI, and XRPUSD_RTI returned HTTP
400. CF Benchmarks' product pages state that real-time or historical data access
for powering a product or service requires licensing contact. No authentication,
license, paywall, or access control was bypassed. The research provider fails
with `CF_DATA_UNAVAILABLE` and has no Yahoo fallback.

Thus direct synchronized Yahoo-versus-CF observations and the exact underlying
one-second ending sequence remain unavailable.

## Measured official-target reference gap

Kalshi public finalized market records supply the official starting target. The
existing Yahoo proxy reference can therefore be compared with that official
target, although it is not a synchronized instantaneous CF observation.

| Asset | n | Mean signed | Median signed | Median abs | P95 abs | P99 abs | Max abs | Correlation |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| BTC | 2,639 | -0.83 bp | -0.79 | 1.25 | 5.24 | 9.01 | 29.64 | 0.99983 |
| ETH | 2,636 | -0.83 bp | -0.80 | 1.68 | 6.85 | 11.82 | 65.52 | 0.99973 |
| SOL | 2,638 | -1.33 bp | -1.34 | 2.11 | 7.28 | 11.82 | 36.21 | 0.99986 |
| XRP | 2,635 | -1.25 bp | -0.96 | 1.88 | 6.94 | 11.45 | 63.99 | 0.99994 |

The proxy is below the exact target at the starting boundary in roughly
65–71% of windows. This is a comparison with the official starting aggregate,
not proof of contemporaneous current-value error later in the window.

## Yahoo terminal proxy versus official expiration value

Kalshi's `expiration_value` is the official finalized ending aggregate. Comparing
the Yahoo terminal proxy with it measures a combined source-plus-aggregation
difference; it does **not** isolate a CF terminal observation from a CF average.

| Asset | n | Median abs | P95 abs | P99 abs | Max abs | Target-side flip |
|---|---:|---:|---:|---:|---:|---:|
| BTC | 2,639 | 1.25 bp | 5.24 | 9.01 | 29.64 | 7.05% |
| ETH | 2,636 | 1.68 bp | 6.79 | 11.82 | 65.52 | 6.18% |
| SOL | 2,638 | 2.11 bp | 7.28 | 11.82 | 36.21 | 7.43% |
| XRP | 2,634 | 1.88 bp | 6.97 | 11.45 | 63.99 | 8.58% |

Flip rates are 7.55%, 7.39%, 7.92%, and 4.52% in the fixed <1 bp, 1–2 bp,
2–5 bp, and >=5 bp starting-distance bands. Low-volatility windows have a
higher combined flip rate (8.52%) than medium (6.91%) or high (6.35%). These
relationships are descriptive, not new gates.

## Settlement reproduction

For 10,547 matched contracts with an official `expiration_value`, applying
`expiration_value >= floor_strike` reproduced the finalized Kalshi result in
10,547/10,547 cases. One matched XRP contract lacked `expiration_value` and is
reported unavailable. Across the larger 39,935-market archive, six rows lack
the official aggregate and remain explicitly listed in the provenance manifest.

This validates result/target arithmetic and equality semantics. It is not an
independent one-second reconstruction because licensed RTI observations are
unavailable.

## Fixed entry cohort and sensitivity

The previously opened Step 5 cohort reproduces exactly: 1,377 entries from
2,111 eligible contracts, 65.2297% coverage. Actual contemporaneous CF values
are unavailable, so actual robustness is reported as:

- `REFERENCE_ROBUST`: 0 known
- `REFERENCE_AMBIGUOUS`: 0 known
- `REFERENCE_FLIPPED`: 0 known
- `REFERENCE_UNKNOWN`: 1,377

No unknown entry is called robust. Symmetric hypothetical perturbations produce:

| Bound | Robust | Ambiguous |
|---|---:|---:|
| ±1 bp | 1,377 | 0 |
| ±2 bp | 1,363 | 14 |
| ±5 bp | 1,256 | 121 |
| ±10 bp | 839 | 538 |

Using each asset's measured P95 starting-target gap only as a sensitivity bound
(not actual current CF error) leaves 1,165 robust and 212 ambiguous entries.
No sensitivity case is labelled an actual flip.

## Exact 60-second reconstruction status

The code validates ordering, uniqueness, exact `[end-60s,end)` coverage,
benchmark identity, count=60, arithmetic mean, and equality semantics for any
future authorized RTI sequence. There are currently zero licensed historical
one-second sequences, so:

- Exact same-source terminal-versus-average distribution: `UNRESOLVED`
- Missing-observation behavior: fail closed
- Exact independently reconstructed windows: 0

## Data chronology

The reused dataset ends at the previously opened Step 5 holdout boundary.
There are no eligible reconstructed rows strictly afterward:

`NEW_STEP7_FORWARD_SAMPLE = INSUFFICIENT_DATA`

All reused holdout evidence is labelled
`PREVIOUSLY_OPENED_HOLDOUT_NOT_NEW_SEALED`.

## Limitations

- Direct licensed CF RTI history is unavailable.
- Current decision-time Yahoo/CF error cannot be measured synchronously.
- Yahoo terminal versus official expiration combines venue/source and averaging
  differences.
- The one-second ending average cannot be independently reconstructed.
- Yahoo one-minute bar timestamp/publication semantics are coarser than CF RTI.
- Six archive markets and one matched contract lack official expiration values.
- Historical data cover a limited recent regime.
- The Step 5 holdout was previously opened.
- Sensitivity bounds are not observations of actual errors.

## Readiness decision

`SETTLEMENT_REFERENCE_PARTIALLY_VALIDATED`
