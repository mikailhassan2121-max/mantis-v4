# Step 8 — Kalshi reference-risk shadow policy

Policy: `KALSHI_REFERENCE_RISK_V1_SHADOW`
Status: `RESEARCH_SHADOW_ONLY`

## Method

Every state uses the actual Kalshi `floor_strike` as target and the causal Yahoo
price as `YAHOO_PROXY`. Distance is:

`10000 * (proxy_current - kalshi_target) / kalshi_target`

For an explicitly stated symmetric uncertainty band `b`, Step 8 evaluates the
side at `distance-b` and `distance+b`:

- `REFERENCE_ROBUST`: both endpoints retain the proxy-implied side.
- `REFERENCE_AMBIGUOUS`: the interval touches or crosses the target.
- `REFERENCE_UNKNOWN`: target, proxy, or bound is unavailable/invalid.

ROBUST means robust to that hypothetical band only. It does not claim the
unobserved CF RTI value is known. No UNKNOWN state is promoted to ROBUST.

Fixed bands are ±1, ±2, ±5, and ±10 bp. Asset P50/P95/P99 bands are derived
only from Step 5 development-window proxy/official-target gaps and then frozen:

| Asset | P50 | P95 | P99 |
|---|---:|---:|---:|
| BTC | 1.29 bp | 5.47 | 9.11 |
| ETH | 1.73 bp | 7.03 | 11.67 |
| SOL | 2.21 bp | 7.58 | 11.68 |
| XRP | 1.84 bp | 6.52 | 10.26 |

Normal-Z, probability, LCB, fragility, disagreement, crossing, and T-300 gates
remain unchanged. Step 8 filters the already fixed cohort only for research.

## Fixed Step 4 cohort

The previously opened holdout reproduces 1,377 entries from 2,111 eligible
asset-contracts (65.2297% original coverage). It remains labelled
`PREVIOUSLY_OPENED_HOLDOUT_NOT_NEW_SEALED`.

| Bound | Robust | Ambiguous | Retained | Coverage of eligible | Accuracy |
|---|---:|---:|---:|---:|---:|
| ±1 bp | 1,377 | 0 | 100.00% | 65.23% | 97.39% |
| ±2 bp | 1,363 | 14 | 98.98% | 64.57% | 97.36% |
| ±5 bp | 1,256 | 121 | 91.21% | 59.50% | 97.61% |
| ±10 bp | 839 | 538 | 60.93% | 39.74% | 97.38% |
| Development P50 | 1,377 | 0 | 100.00% | 65.23% | 97.39% |
| Development P95 | 1,168 | 209 | 84.82% | 55.33% | 97.43% |
| Development P99 | 788 | 589 | 57.23% | 37.33% | 97.72% |

Validation did not improve: original accuracy was 98.67%; ±5 bp produced
98.62%, P95 98.63%, and P99 98.46%. The P99 opened-holdout increase therefore
does not establish a useful rule.

For the opened holdout, ±5 bp retained YES/NO accuracy of 98.22%/97.12%; P95
retained 98.07%/96.92%. P95 per-asset retained results were BTC 238 at 97.06%,
ETH 255 at 97.65%, SOL 337 at 98.22%, and XRP 338 at 96.75%.

## Near-target and regime evidence

Opened-holdout fixed entries contain no `<1 bp` entries. Results are:

| Distance | n | Accuracy |
|---|---:|---:|
| 1–2 bp | 14 | 100.00% |
| 2–5 bp | 107 | 94.39% |
| 5–10 bp | 417 | 98.08% |
| >=10 bp | 839 | 97.38% |

The weak 2–5 bp bucket is consistent with reference-risk concern, but accuracy
is non-monotonic and the sample is reused opened-holdout evidence.

By entry time, accuracy ranged from 95.19% at T-180–120 to 99.62% in the final
60 seconds. By development-frozen volatility regime it was 96.68% low, 97.62%
medium, and 98.86% high. All fixed entries had crossing probability <=0.10,
so crossing-risk heterogeneity cannot be assessed inside this cohort. Most
entries were in the existing fragility 25–50 band.

## Combined endpoint error

The Step 7 terminal Yahoo proxy versus official Kalshi `expiration_value`
analysis is retained under the exact label
`COMBINED_SOURCE_PLUS_AVERAGING_ERROR`. It does not isolate 60-second averaging.
Asset-side flip rates remain BTC 7.05%, ETH 6.18%, SOL 7.43%, XRP 8.58%.

Within opened-holdout entries, combined target-side flip rates by entry bucket
were 1.15% at T-60–0, 2.42% at T-120–60, 3.74% at T-180–120, 3.78% at
T-240–180, and 2.60% at T-300–240. These are outcome-time combined-error
comparisons grouped by entry timing, not observed decision-time CF errors.

## Shadow diagnostic

`format_shadow_diagnostic()` renders target, proxy, distance, risk state, model
side, and `ACTIONABILITY ........ SHADOW ONLY`. It is an isolated formatter and
is not connected to the live UI, selector, economics, voice, or forward store.

## New data

No causal finalized cohort exists after the opened Step 5 boundary:

`NEW_FORWARD_SAMPLE_INSUFFICIENT`

## Interpretation

A target exclusion zone is directionally plausible, especially around 2–5 bp,
but the available evidence does not select a production bound. Development and
validation do not show stable accuracy improvement, and more conservative bands
discard substantial coverage. Direct synchronized CF observations remain absent.

Therefore Step 8 does not authorize a production Kalshi reference policy.

## Limitations

- Direct decision-time CF RTI values remain unavailable.
- Empirical bounds measure Yahoo starting-reference versus official Kalshi target,
  not synchronized current-value error.
- Endpoint comparisons combine source and averaging effects.
- The holdout was previously opened.
- No new forward cohort exists.
- Results cover a limited recent regime.
- Candidate filters were not optimized, and no threshold was selected.
- Classification accuracy does not establish profitability.
