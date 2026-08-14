# MANTIS — PHASE 3 BASELINE RESULTS

**Date:** 2026-08-13 · **Contracts:** 13,395 (5 assets × 2,679 windows, 28 days)
**Settlement:** `PROXY_TERMINAL_ABOVE_REFERENCE` — **UNVERIFIED**

> ## THIS DOCUMENT REPORTS CLASSIFICATION ACCURACY ONLY.
>
> **Economic profitability is NOT EVALUABLE.** There are no historical event-contract prices, so
> there is no EV, no P&L, no break-even and no return figure anywhere in this report — the metrics
> objects have no field for them.
>
> **Do not infer profitability from accuracy.** A rule that only trades contracts priced at $0.95 can
> be 94% accurate and lose money on every trade. Nothing below distinguishes those cases, because
> nothing below can.
>
> All results are **PRELIMINARY**: 28 days, one market regime, proxy references, no model trained.

---

## 1. HEADLINE

| # | Baseline | Traded | Coverage | Accuracy | Naive 95% CI | **Dependence-adjusted 95% CI** | Brier | Log loss |
|---|---|---:|---:|---:|---|---|---:|---:|
| A | spot vs reference | 12,450 | 92.95% | 63.01% | [62.16, 63.86] | **[61.57, 64.46]** | N/A | N/A |
| B | **V3 shipped logic** | 13,317 | 99.42% | 74.45% | [73.70, 75.18] | **[73.15, 75.68]** | 0.2011 | 0.6102 |
| C | normal-Z baseline | 12,676 | 94.63% | 84.80% | [84.16, 85.41] | **[83.72, 85.85]** | 0.1280 | 0.4224 |
| D | Gemini 20pp / under-5m | — | — | — | — | — | — | — |

**Base rate:** 48.32% YES. Always-NO would score 51.68%, which is the floor any result must clear.

**Baseline D is NOT EVALUABLE.** Its trigger is defined on `model_edge = p_model − p_implied`, and
`p_implied` requires an executable historical contract ask. None exists. Per Phase 3 requirement 11
it is reported as not evaluable rather than approximated; `GeminiEdgeStrategy.decide()` raises rather
than substituting a synthetic ask, and the backtester refuses to replay it. Note that the *temporal*
half of the rule (< 300 s) is measurable and appears in the entry-time buckets below — it is the
*economic* half, the half that determines profitability, that cannot be tested.

### The dependence-adjusted column is the one to read

The measured cross-asset correlation (effective 1.72 independent assets of 5) implies a design effect
of 2.91× and a CI-widening factor of 1.71×. Effective sample size is ~4,400 contracts, not 13,395.
The naive intervals assume independence and are **too narrow**. Both are shown so the correction is
auditable; the adjusted column is the honest one.

---

## 2. THE PRINCIPAL FINDING: V3'S TREND TERM IS ACTIVELY HARMFUL

Phase 1 finding B-2 argued from arithmetic that V3's `adjusted_z = raw_z + trend_adjustment` was an
unvalidated drift assumption supplying up to 65.4% of the evidence needed to fire a signal. Phase 3
tests that claim on 13,317 out-of-sample trades.

**Split V3's own trades by whether the trend term was load-bearing:**

| V3 entries | n | Accuracy | 95% CI |
|---|---:|---:|---|
| **Trend-dependent** (`\|raw_z\| < 0.8416` but `\|adjusted_z\| ≥ 0.8416`) | 10,468 | **71.74%** | [70.87, 72.60] |
| **Buffer-sufficient** (`\|raw_z\| ≥ 0.8416` on its own) | 2,849 | **84.38%** | [83.00, 85.67] |

**78.6% of V3's trades only qualified because the trend term pushed them over the line**, and those
trades are 12.6 points worse. The confidence intervals do not overlap.

Accuracy degrades monotonically with how much the trend term contributed:

| Trend contribution quintile | n | Median trend_help | Accuracy | 95% CI |
|---|---:|---:|---:|---|
| least | 2,976 | +0.315 | **81.59%** | [80.2, 82.9] |
| q2 | 2,525 | +0.375 | 75.13% | [73.4, 76.8] |
| q3 | 2,493 | +0.530 | 71.32% | [69.5, 73.1] |
| q4 | 3,919 | +0.550 | 71.75% | [70.3, 73.1] |
| most | 1,404 | +0.550 | **71.15%** | [68.7, 73.5] |

The more V3 relied on trend rather than on an actual buffer, the worse it did. This is as clean a
refutation of a feature as this dataset can produce.

### Head-to-head, same contracts

On the 12,671 contracts where both B and C traded:

|  | count |
|---|---:|
| C correct, B wrong | **1,467** |
| B correct, C wrong | **288** |
| both correct | 9,278 |
| both wrong | 1,638 |

- **McNemar** χ² = 790.7, p = 5.7 × 10⁻¹⁷⁴
- **Bootstrap accuracy difference** (4,000 resamples): **+9.30 pp**, 95% CI **[+8.70, +9.94]**

C is better than V3 by a margin far outside chance. The 5:1 ratio of discordant pairs is the whole
story: removing the trend term is what does it.

**What this does NOT establish:** that C is *profitable*, or that C should be the production rule.
See §4.

---

## 3. BREAKDOWNS

### By asset — no asset carries the result

| Asset | A acc | B acc | C acc | Base YES |
|---|---:|---:|---:|---:|
| BTC-USD | 61.4% | 75.9% | 85.4% | 50.0% |
| ETH-USD | 63.4% | 74.5% | 85.4% | 50.3% |
| SOL-USD | 63.4% | 74.9% | 84.7% | 48.7% |
| ADA-USD | 63.9% | 73.2% | 84.3% | 45.5% |
| XRP-USD | 63.1% | 73.8% | 84.2% | 47.1% |

Tight and consistent. Given the 0.64 return correlation, though, these five columns are **not five
independent confirmations** — closer to two.

### By direction — a mild but consistent asymmetry

| Strategy | YES acc | NO acc |
|---|---:|---:|
| A | 61.6% | 64.5% |
| B | 73.2% | 75.7% |
| C | 83.7% | 85.9% |

NO beats YES by 2–3 points in all three baselines, consistent with the 48.3% base rate. Worth
carrying into Phase 5 as a candidate asymmetry, not yet as a conclusion.

### By entry time — accuracy rises as expiry approaches

| Bucket | A | B | C |
|---|---:|---:|---:|
| T-900..600s | 63.0% (n=12,450) | 72.1% (n=7,330) | 82.6% (n=2,837) |
| T-600..450s | — | 76.5% (n=3,584) | 85.6% (n=3,418) |
| T-450..300s | — | 78.3% (n=1,303) | 84.4% (n=2,465) |
| T-300..150s | — | 78.6% (n=864) | 85.5% (n=2,642) |
| T-150..0s | — | 80.5% (n=236) | **86.8%** (n=1,314) |

**This is the single most important table for not fooling yourself.** Accuracy rises monotonically as
the contract approaches resolution — because less time remains for price to cross back. That is
mechanical, not skill.

And it is precisely where accuracy and profitability diverge hardest: a contract that is 87% likely
with 90 seconds left will be *priced* near $0.87. Buying it may have **zero or negative edge** after
spread and fees. The higher-accuracy bucket may well be the *less* profitable one. Section 26H's
"under 5 minutes" intuition is directionally supported on accuracy and **completely untested on
economics**.

### By volatility — higher vol is mildly better, not worse

| Bucket | A | B | C |
|---|---:|---:|---:|
| vol_q1 (lowest) | 60.9% | 73.0% | 83.4% |
| vol_q2 | 64.2% | 74.5% | 84.7% |
| vol_q3 (highest) | 63.9% | 75.9% | **86.3%** |

Mildly counter-intuitive, and worth a Phase 5 look: a higher σ enlarges the denominator of z, so a
qualifying |z| in a high-vol regime requires a proportionally larger buffer — the filter is
self-tightening. That is a hypothesis, not a finding.

### Audit B-3 re-confirmed on real data

`MIN_BUFFER_Z` bound **0 times** in 13,317 V3 trades. The Phase 1 proof that the 0.55 buffer gate is
mathematically unreachable behind the 0.80 confidence gate (which implies |z| ≥ 0.8416) is confirmed
empirically. Anyone tuning `MIN_BUFFER_Z` in V3 was adjusting a no-op.

---

## 4. WHAT THESE RESULTS DO AND DO NOT MEAN

**Supported:**

1. V3's trend term degrades V3's own accuracy, substantially and with overwhelming statistical
   significance. Audit finding B-2 is confirmed out of sample.
2. `MIN_BUFFER_Z` is dead code. Audit B-3 confirmed empirically.
3. A transparent Φ(buffer / σ_remaining) rule beats V3 by +9.30 pp [+8.70, +9.94] on identical
   contracts.
4. Crypto assets are strongly dependent; per-coin independence is false (see dataset notes §3).
5. Accuracy rises as expiry approaches, mechanically.

**NOT supported — and I want to be blunt about these:**

1. **That any baseline is profitable.** Unknown and unknowable here. C's 84.8% accuracy at a median
   450 s remaining is exactly the regime where contracts are expensive.
2. **That C should be the production rule.** C is a *baseline*, and it is uncalibrated. A Brier of
   0.128 is much better than B's 0.201, but Brier ≠ calibration. Reliability curves are Phase 5.
3. **That these numbers will hold.** 28 days, one regime, effective n ≈ 4,400, proxy references.
4. **That C's coverage is free.** C abstained on 5.37% of contracts; whether abstention is where the
   value is cannot be judged without prices.
5. **Anything about the Gemini rule's economics.** Not evaluable.

**A caution I would flag even though it wasn't asked for:** it is tempting to read "C = 84.8%" as
"MANTIS is 85% accurate". It is not. C traded 94.6% of all contracts including many where it merely
had to observe that price was already far from the reference with little time left. The interesting
question — *is there edge after the contract's price?* — is untouched by every number in this report.

---

## 5. WHAT PHASE 4+ SHOULD DO WITH THIS

1. **Do not carry V3's trend term forward as-is.** It is refuted. If a momentum feature belongs, it
   must be re-derived and calibrated (§26F), not inherited.
2. **Use C as the transparent benchmark to beat** (§7, §26D), and treat +9.30 pp over V3 as the bar.
3. **Group walk-forward folds by window**, not by row (dataset notes §3).
4. **Adjust every interval for the 2.91× design effect**, or report effective n.
5. **Get contract prices.** Until then Phases 8, 11, 13 and 26G/H/M stay blocked, and MANTIS cannot
   distinguish an accurate rule from a profitable one — which is the entire point of the system.
6. **Treat the entry-time result as a warning, not a strategy.** "Enter later, be more accurate" is
   almost certainly "enter later, pay more".

---

## 6. REPRODUCING

```bash
python mantis_v4_backtest.py --days 28 --scan-interval 15
python mantis_v4_backtest.py --days 28 --scan-interval 15 --paranoid   # + per-view leakage assertions
python -m unittest discover -s tests -t .
```

History chunks are cached under `data/history/`, so re-runs replay byte-identical input.
Full numeric output: `data/mantis_v4_phase3_summary.json`.
