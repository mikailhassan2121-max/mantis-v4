# Step 4 - Kalshi-native reference policy

## Status

`KALSHI_REFERENCE_V1` is a separately versioned **experimental research policy**. It is not
connected to `PRIMARY_SELECTOR_V1`, actionable voice, or forward primary-selection records.
The original `H_p0.95_l0.90_f50_d.05_t300` policy and its proxy-reference results are unchanged.

## Event and data provenance

The modeled event is:

`KALSHI_ENDING_60S_CF_BENCHMARK_AVERAGE >= KALSHI_STARTING_TARGET`

- Starting reference: actual `floor_strike` supplied by the current/historical Kalshi market.
- Settlement provenance: `KALSHI_CRYPTO15M_CF_BENCHMARKS`.
- Current value: `YAHOO_1M_CLOSE_PROXY_FOR_CF_BENCHMARKS`.
- Endpoint treatment: `TERMINAL_PRICE_CONSERVATIVE_PROXY_NO_SQRT60`.

CF Benchmarks documents a REST values API, but anonymous calls on this machine did not expose a
usable BRTI/ETH/SOL/XRP stream: the public index directory returned no streams and `BRTI` returned
`Unknown id`. CF Benchmarks also directs product/service users to licensing for real-time or
historical index data. Kalshi market metadata does not expose a current in-window RTI value.
Consequently V1 uses the already-hardened Yahoo causal close as a clearly labelled proxy; it never
calls that value CF Benchmarks data. Alpaca was not selected because it would add credentials and a
new provider without establishing equivalence to the multi-venue CF benchmark.

The endpoint is a 60-observation average. V1 does **not** divide volatility by `sqrt(60)`: adjacent
one-second index levels are strongly overlapping price levels, not independent returns, and no
effective-sample-size estimate has been validated. Retaining terminal-price variance is a deliberate,
usually less-confident approximation away from the target. It remains a limitation, not a claim of
exact endpoint-average modeling.

## Policy identity

- Model/input policy: `KALSHI_REFERENCE_V1`
- Qualification policy: `KALSHI_H_V1`
- Gate status: `TRANSFERRED_UNVALIDATED_GATES`
- Selection policy reserved for later: `PRIMARY_SELECTOR_V1`

The `.95` confidence, `.90` conservative bound, fragility `50`, disagreement `.05`, T-300,
crossing-probability `.35`, and four-crossing gates were transferred without retuning.

## Historical reconstruction

Official anonymous endpoints used:

- `GET /markets?series_ticker=...&status=settled`
- `GET /historical/markets?series_ticker=...`

The bounded capture used at most five 1,000-row pages per live/archive tier and series. It
reconstructed 39,935 finalized contracts from 2026-04-23 through 2026-08-15. Of those, 10,548
contracts matched the repository's cached causal one-minute Yahoo period, producing 168,768 state
rows. The public archive is deeper than the locally available minute-bar history.

Matched windows were split chronologically and by shared cross-asset window:

- Development: 1,585 windows, 6,335 asset-contracts.
- Validation: 528 windows, 2,102 asset-contracts.
- Sealed holdout: 529 windows, 2,111 asset-contracts.
- Holdout window hash: `e96d66b3f17ade48`.

No thresholds were selected or changed using any split. The holdout was inspected once after the
transferred policy and endpoint approximation were fixed.

## Reference differences

| Asset | Contracts | Mean signed gap | Median absolute gap | 95th pct absolute gap |
|---|---:|---:|---:|---:|
| BTC | 2,639 | +0.83 bps | 1.25 bps | 5.24 bps |
| ETH | 2,636 | +0.83 bps | 1.68 bps | 6.86 bps |
| SOL | 2,638 | +1.33 bps | 2.11 bps | 7.28 bps |
| XRP | 2,635 | +1.25 bps | 1.88 bps | 6.94 bps |

Using the Kalshi target changed the raw predicted side on 10.59% of all scan states. Among contracts
where both transferred policies entered, selected side differed on 0.23%. Whether a contract
qualified changed on 11.71% of asset-contracts.

## Transferred-gate evaluation

| Split | Entries | Coverage | Accuracy | Clustered 95% CI | Brier | Log loss |
|---|---:|---:|---:|---:|---:|---:|
| Development | 4,134 | 65.26% | 98.72% | 98.29-99.10% | 0.01276 | 0.07379 |
| Validation | 1,351 | 64.27% | 98.67% | 97.73-99.28% | 0.01331 | 0.07267 |
| Sealed holdout | 1,377 | 65.23% | 97.39% | 96.39-98.32% | 0.02545 | 0.12490 |

Holdout YES accuracy was 98.04% (611 entries); NO accuracy was 96.87% (766 entries).

| Asset | Holdout entries | Accuracy |
|---|---:|---:|
| BTC | 323 | 96.59% |
| ETH | 334 | 97.90% |
| SOL | 361 | 98.34% |
| XRP | 359 | 96.66% |

Holdout calibration by entry-confidence band:

- 0.950-0.975: n=398, mean confidence 96.36%, empirical accuracy 95.98%.
- 0.975-1.000: n=979, mean confidence 99.11%, empirical accuracy 97.96%.

The highest-confidence band is overconfident relative to realized accuracy. These figures are
classification results under a short recent regime and a current-value/endpoint proxy. They do not
establish profitability or justify live action.

## Kalshi fee model

`KalshiFeeModel` implements the official immediately-matched general quadratic calculation:

`ceil_to_cent(0.07 * fee_multiplier * contracts * price * (1-price))`

The schedule identity is `KALSHI_OFFICIAL_FEE_SCHEDULE_2026-07-07`. Current official series API
metadata checked 2026-08-15 reports `fee_type=quadratic`, `fee_multiplier=1`, and no historical or
scheduled fee changes for each of KXBTC15M, KXETH15M, KXSOL15M, and KXXRP15M. Unsupported fee types
fail closed as `FEE_UNVERIFIED`. Fees are rounded once for the whole transaction, upward to the next
cent. The Webull fixed $0.02 fee is not used.

## Decision

`KALSHI_REFERENCE_V1` is implemented and has encouraging transferred-gate classification results,
but it is **not sufficiently validated for actionable live economics**. Remaining blockers:

1. Obtain licensed/direct CF Benchmarks current RTI values, or validate the Yahoo-to-RTI proxy error
   with synchronized observations.
2. Validate the terminal-price approximation against the actual ending 60-second average, including
   an effective-sample-size/autocorrelation study.
3. Accumulate a longer regime sample and independently confirm the observed high-confidence
   overconfidence before authorizing live `PRIMARY_SELECTOR_V1` integration.

Generated evidence is stored in ignored research-data artifacts:
`data/kalshi_reference_v1_markets.csv` and `data/kalshi_reference_v1_summary.json`.
