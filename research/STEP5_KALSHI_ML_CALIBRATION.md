# Step 5 - Kalshi ML baselines and probability calibration

## Status and boundary

`KALSHI_PROBABILITY_RESEARCH_V1` is research/shadow-only. It is not connected to live selection,
actionable UI state, voice, economics, forward primary-selection records, or execution. Step 4's
`KALSHI_REFERENCE_V1`, `TRANSFERRED_UNVALIDATED_GATES`, Yahoo/CF Benchmarks limitation, and endpoint
average limitation remain in force.

## Causal feature schema

| Feature | Decision-time meaning |
|---|---|
| `kalshi_buffer_pct` | Causal current close minus already-published Kalshi target, divided by target |
| `kalshi_normal_z` | Target-relative buffer divided by causal remaining volatility |
| `log_seconds_remaining` | Exchange window clock at scan time |
| `realized_vol_1m` | Returns ending no later than scan time |
| `rv_5m`, `rv_15m`, `rv_30m` | Causal realized-volatility lookbacks |
| `mom1`, `mom3`, `mom5`, `mom10` | Causal returns over named lookbacks |
| `momentum_acceleration` | Difference of causal short-lookback returns |
| `vol_ratio_short_long` | Causal short/long volatility ratio |
| `crossings` | Crossings of the actual Kalshi target observed by scan time |
| `reference_gap_bps` | Published Kalshi target versus proxy window open, both available by scan time |
| `elapsed_fraction` | Deterministic window-clock position |
| `hour_of_day_sin`, `hour_of_day_cos`, `is_weekend` | Deterministic timestamp encodings |
| `asset_BTC`, `asset_ETH`, `asset_SOL`, `asset_XRP` | Static asset identity |

The feature builder is a hard allow-list. Names containing outcome, result, settlement, terminal,
expiration value, ending benchmark, future, correctness, or label semantics are rejected. Labels are
passed separately. Non-finite values, non-positive targets/volatility, and state/table misalignment
fail closed.

## Split and fitting discipline

All BTC/ETH/SOL/XRP rows sharing a quarter-hour window stay in the same chronological group.

| Split | UTC range | Windows/groups | State rows |
|---|---|---:|---:|
| Development | 2026-07-17 02:15 to 2026-08-02 18:45 | 1,585 | 101,360 |
| Validation | 2026-08-02 19:00 to 2026-08-08 09:00 | 528 | 33,632 |
| Sealed holdout | 2026-08-08 09:15 to 2026-08-13 23:45 | 529 | 33,776 |

Within development, the early 75% of windows fit base models and the late 25% fit Platt/isotonic
calibrators. Candidate selection used validation all-state Brier, then log loss, then predeclared
model order. After selection, the base model was refit on development and its calibrator on
validation. The model and freeze manifest were serialized before the holdout block executed.

The frozen Step 4 qualification cohort is reused for every entry-time comparison. Models cannot
improve reported entry accuracy by taking fewer contracts. Gates and coverage never change.

## Models tested

1. Existing transparent statistical Normal-Z probability.
2. L2 logistic regression with standardized features (`C=1`, LBFGS, seed 20260815).
3. Shallow histogram gradient boosting (`max_depth=3`, 100 iterations, learning rate 0.05,
   L2=0.1, seed 20260815).

Each was evaluated raw, with Platt calibration, and with isotonic calibration. No deep learning,
hyperparameter sweep, ensemble, or gate tuning was performed.

## Validation results

| Model | Calibration | Brier | Log loss | Accuracy |
|---|---|---:|---:|---:|
| Histogram gradient boosting | **Platt** | **0.134477** | **0.408588** | 79.98% |
| Histogram gradient boosting | Isotonic | 0.134668 | 0.408743 | 79.93% |
| Histogram gradient boosting | Raw | 0.134955 | 0.409930 | 79.80% |
| Statistical | Isotonic | 0.135630 | 0.411765 | 79.69% |
| Logistic | Raw | 0.135631 | 0.411848 | 79.48% |
| Logistic | Platt | 0.135645 | 0.411688 | 79.65% |
| Logistic | Isotonic | 0.135726 | 0.412088 | 79.59% |
| Statistical | Platt | 0.135875 | 0.412999 | 79.81% |
| Statistical | Raw | 0.137302 | 0.416761 | 79.54% |

The frozen candidate is `hist_gradient_boosting + platt`. It won on the precommitted primary metric
and also had the best validation log loss. Validation fixed-entry coverage remained 64.27%.

## Single sealed-holdout comparison

### All decision states

| Model | Brier | Log loss | Accuracy |
|---|---:|---:|---:|
| Step 4 statistical baseline | 0.140167 | 0.424088 | 79.24% |
| Frozen HGB + Platt | **0.135963** | **0.412897** | **79.66%** |
| Candidate minus baseline | **-0.004204** | **-0.011191** | **+0.42 pp** |

This is a real numerical out-of-sample improvement on the sealed sample. Its across-regime
robustness and clustered uncertainty have not yet been established, so it is not evidence for live
actionability.

### Fixed Step 4 entry cohort

Coverage was identical at 65.23%: 1,377 entries from 2,111 asset-contracts.

| Model | Brier | Log loss | Accuracy |
|---|---:|---:|---:|
| Step 4 statistical baseline | 0.025451 | 0.124895 | 97.39% |
| Frozen HGB + Platt | 0.025444 | **0.121595** | 97.39% |
| Candidate minus baseline | -0.000007 | -0.003300 | 0.00 pp |

At qualified entry points, Brier is effectively tied and accuracy is identical. Step 5 therefore
does not demonstrate a material improvement in the existing qualified-entry decision cohort.

## Holdout calibration

All-state frozen-candidate reliability:

| Confidence band | n | Mean confidence | Empirical accuracy |
|---|---:|---:|---:|
| 0.50-0.60 | 5,765 | 55.03% | 55.52% |
| 0.60-0.70 | 5,850 | 64.92% | 65.25% |
| 0.70-0.80 | 4,621 | 74.77% | 75.74% |
| 0.80-0.90 | 4,329 | 85.13% | 83.99% |
| 0.90-0.95 | 2,940 | 92.50% | 91.12% |
| 0.95-0.975 | 2,563 | 96.59% | 95.47% |
| 0.975-1.00 | 7,708 | 99.08% | 98.92% |

The top band is much closer than Step 4's entry cohort but mild overconfidence remains in several
high-confidence bands.

## Holdout per-asset all-state results

| Asset | n | Accuracy | Brier |
|---|---:|---:|---:|
| BTC | 8,464 | 78.63% | 0.140713 |
| ETH | 8,432 | 79.91% | 0.131744 |
| SOL | 8,448 | 79.63% | 0.138186 |
| XRP | 8,432 | 80.47% | 0.133187 |

Predicted-side accuracy was 80.72% for YES and 78.60% for NO on all holdout states. On the fixed
entry cohort it remained exactly the Step 4 result: YES 98.04%, NO 96.87%.

## Reproducibility identities

- Data hash: `b1f07f06957f958cb5bcaea946bd738b98d233745d49658ae389b7b8584c8008`
- Feature schema hash: `67430b88f191d72348d608806d9cacb16c0bab88d23ef38db4454cb940521e88`
- Configuration hash: `584f86c02f1353f6cf2006f4d3aeace244f9f8c112435ebec5788aee84ca7f7e`
- Frozen model hash: `e550c9df19ea8e3cb6b0557c1349092c911b6fe01717bc78d7d1fae47a5fd57f`
- Freeze manifest hash: `e288d5f4664c6bc0084d7d96d981951ddd2616a6dd58b1deab8a9dd92fb3787a`
- Random seed: `20260815`

Ignored research artifacts:

- `data/models/kalshi_step5_frozen.joblib`
- `data/kalshi_step5_freeze.json`
- `data/kalshi_step5_summary.json`

## Conclusion

Step 5 demonstrates a modest sealed out-of-sample probability improvement across all decision
states, driven by shallow nonlinear modeling plus Platt calibration. It does **not** demonstrate a
material Brier or accuracy improvement on the frozen qualified-entry cohort. The result remains
shadow-only because CF Benchmarks current-value equivalence, endpoint-average modeling, broader
regime validation, and clustered uncertainty remain unresolved.
