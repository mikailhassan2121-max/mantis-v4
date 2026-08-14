# Phase 6 Entry Policy

> **PROXY SETTLEMENT REFERENCE**
> **NO HISTORICAL WEBULL CONTRACT QUOTES**
> **CLASSIFICATION RESEARCH ONLY**
> **NOT A PROFITABILITY BACKTEST**
> **LIMITED RECENT HISTORICAL REGIME**

Normal-Z remains the unchanged directional probability anchor. Student-t is used only to form heavy-tail disagreement and a conservative eligibility bound. Sensitivities are fragility diagnostics, never alpha. All hypothetical entries are held to fixed resolution and occur once per contract/asset. `EV_STATUS = UNAVAILABLE`.

## Untouched holdout results

| Family | Locked development policy | Trades | Coverage | Accuracy | Clustered 95% CI | Abstention |
|---|---|---:|---:|---:|---:|---:|
| A_probability_only | A_p0.95 | 2607 | 77.82% | 95.17% | [94.21%, 96.14%] | 22.18% |
| B_probability_lcb | B_p.95_l0.70 | 2607 | 77.82% | 95.17% | [94.21%, 96.14%] | 22.18% |
| C_probability_fragility | C_p0.95_f50 | 2211 | 66.00% | 96.11% | [95.19%, 97.10%] | 34.00% |
| D_probability_disagreement | D_p0.95_d.05 | 2607 | 77.82% | 95.17% | [94.21%, 96.14%] | 22.18% |
| E_probability_fragility_disagreement | E_p0.95_f50_d.05 | 2211 | 66.00% | 96.11% | [95.19%, 97.10%] | 34.00% |
| F_probability_lcb_fragility_disagreement | F_p0.95_l0.90_f50_d.05 | 2211 | 66.00% | 96.11% | [95.19%, 97.10%] | 34.00% |
| G_fixed_under_5m | G_p0.95_under300 | 2590 | 77.31% | 95.60% | [94.63%, 96.49%] | 22.69% |
| H_adaptive_timing | H_p0.95_l0.90_f50_d.05_t300 | 2135 | 63.73% | 96.58% | [95.64%, 97.57%] | 36.27% |

## Findings

- Adaptive timing beat fixed under-five-minute timing by +0.98 percentage points of accuracy, while changing coverage by -13.58 points.
- The selected LCB-only policy changed accuracy by +0.00 points versus probability-only; on this holdout the selected LCB gate did not earn an incremental benefit.
- The fragility gate changed accuracy by +0.94 points and coverage by -11.82 points.
- The disagreement-only gate changed accuracy by +0.00 points; at the development-selected threshold it did not earn an incremental benefit.
- The combined fragility+disagreement policy changed accuracy by +0.94 points and coverage by -11.82 points; its result equalled fragility-only, so disagreement did not add value here.
- The recommended rule placed 781/2135 entries (36.58%) at T-120 or later and 168/2135 (7.87%) in the final 30 seconds. Its accuracy is therefore not solely a final-seconds artifact, but remains classification-only and may still reflect late information and collapsed remaining volatility.

## Recommendation for Phase 7

Lock `H_p0.95_l0.90_f50_d.05_t300` for Phase 7 economic evaluation: holdout accuracy 96.58%, coverage 63.73%, clustered CI [95.64%, 97.57%]. This recommendation concerns classification entry quality only.

This is a **CLASSIFICATION-OPTIMAL ENTRY POLICY UNDER PROXY DATA**, not profit-maximizing timing. Phase 7 has not begun.
