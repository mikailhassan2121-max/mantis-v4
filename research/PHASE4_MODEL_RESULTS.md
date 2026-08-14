# MANTIS — PHASE 4 MODEL RESULTS

```
PROXY SETTLEMENT REFERENCE
NO HISTORICAL WEBULL CONTRACT QUOTES
CLASSIFICATION RESEARCH ONLY — NOT A PROFITABILITY BACKTEST
LIMITED RECENT HISTORICAL REGIME
```

**Date:** 2026-08-13 · **V3:** untouched (MD5 `4fcb413eb59e07da1cc98645e2111a6e`)
**Dataset:** 214,313 rows · 13,395 contracts · 5 assets · 28 days · 16 scan points per contract
**Development:** 160,713 rows / 2,009 windows · **Sealed holdout:** 53,600 rows / 670 windows
**Holdout hash:** `edbbbb1feecc9692` · **seal intact: True**

---

## HEADLINE

> ### Nothing beat Normal-Z. Normal-Z is retained.
>
> All four fitted models — logistic, L2-regularized logistic, L1-regularized logistic, and probit —
> were **statistically worse** than Normal-Z on the development folds, with paired clustered
> bootstrap intervals excluding zero. Per Phase 4 section 12 item 7, Normal-Z stays.

On the sealed holdout, Normal-Z produced **Brier 0.1474, log loss 0.4447, calibration slope 1.011,
intercept 0.010, ECE 0.0098**, accuracy **77.90%** (clustered 95% CI **[76.78%, 78.86%]**).

The calibration slope of 1.011 and intercept of 0.010 are close to the ideal (1, 0). **Normal-Z was
already well calibrated before any recalibration was applied** — which is the single most useful
thing Phase 4 learned, and is covered in `PHASE4_CALIBRATION.md`.

---

## 1. MODEL COMPARISON (development folds, out-of-sample, calibrated)

| Model | n | Brier | Log loss | Slope | Intercept | ECE | MCE | Accuracy | Clustered 95% CI |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| **A Normal-Z** | 128,633 | **0.1512** | **0.4564** | 0.962 | −0.074 | 0.0066 | 0.0243 | 76.72% | [75.97%, 77.56%] |
| B logistic | 128,633 | 0.1528 | 0.4576 | 0.983 | +0.058 | 0.0065 | 0.0167 | 76.72% | [75.91%, 77.61%] |
| C logistic L2 | 128,633 | 0.1528 | 0.4576 | 0.984 | +0.059 | 0.0065 | 0.0160 | 76.72% | [75.91%, 77.61%] |
| C logistic L1 | 128,633 | 0.1528 | 0.4575 | 0.984 | +0.054 | 0.0060 | 0.0154 | 76.71% | [75.90%, 77.60%] |
| D probit | 128,633 | 0.1531 | 0.4593 | 0.985 | +0.060 | 0.0049 | 0.0103 | 76.70% | [75.89%, 77.59%] |

All four fitted models used the 60 retained features. Every one of them is worse on Brier and log
loss than a rule with **zero fitted parameters**.

### Paired comparison against Normal-Z (clustered bootstrap, identical rows)

| Model | ΔBrier | 95% CI | Verdict |
|---|---:|---|---|
| B logistic | +0.00161 | [+0.00024, +0.00299] | **WORSE than Normal-Z** |
| C logistic L2 | +0.00163 | [+0.00025, +0.00301] | **WORSE than Normal-Z** |
| C logistic L1 | +0.00156 | [+0.00023, +0.00291] | **WORSE than Normal-Z** |
| D probit | +0.00193 | [+0.00056, +0.00333] | **WORSE than Normal-Z** |

Positive ΔBrier means worse. Every interval excludes zero.

### Why the fitted models lost

This is not a bug and it is worth stating plainly. Normal-Z is Φ(buffer / σ_remaining) — the exact
closed form for a driftless random walk, which is close to the true data-generating process for a
15-minute crypto window. The fitted models must *rediscover* that functional form from a linear
index over 60 features, and a linear-in-features logit cannot represent Φ(x/σ) exactly. They spend
their capacity approximating a shape Normal-Z already has for free, and they pay estimation variance
for the privilege.

Adding features did not help because the signal is essentially all in one place — see the ablation
report.

---

## 2. THE ONE CANDIDATE WITH ANY SIGNAL

The ablation flagged the 10-feature **geometry** set as marginally ahead, so I tested it directly
rather than reading it off a table. The evidence is genuinely mixed:

**Development folds (paired, clustered):**

| Metric | Δ vs Normal-Z | 95% CI | Verdict |
|---|---:|---|---|
| ΔBrier | −0.000293 | [−0.000782, +0.000196] | **not distinguishable** |
| ΔLog loss | −0.002985 | [−0.005000, −0.000948] | distinguishable, geometry better |
| ΔAccuracy | +0.00324 | [+0.00116, +0.00518] | distinguishable, geometry better (+0.32 pp) |

**Sealed holdout:**

| Model | Brier | Log loss | Slope | ECE |
|---|---:|---:|---:|---:|
| geometry L2 (10 features) | 0.1472 | 0.4439 | 1.043 | 0.0141 |
| Normal-Z | 0.1478 | 0.4486 | 0.986 | 0.0194 |

ΔBrier = **−0.000659**, 95% CI **[−0.001332, +0.000039]** → **NOT DISTINGUISHABLE**.

**Verdict: not a robust improvement, so Normal-Z is retained.** The reasoning:

1. Brier — the primary proper scoring rule — fails to distinguish it on *both* dev and holdout.
2. The holdout interval misses zero by 0.00004; on a different 28-day sample it would land either
   side. That is not a result, it is a coin flip dressed as one.
3. The effect is +0.32 percentage points of accuracy. Even taken at face value it is not worth
   trading a zero-parameter rule for a fitted one.
4. Normal-Z cannot overfit. Its out-of-sample number is its in-sample number.

It is, however, the only candidate that showed *anything*, and its features are the ones Normal-Z
already uses plus time. Phase 5 should revisit it with nonlinear models rather than discard it.

---

## 3. SELECTIVE PREDICTION — "how accurate if we get pickier?"

Development folds, Normal-Z, out-of-sample:

| Threshold | Coverage | Trades | Windows | Accuracy | Clustered 95% CI | YES | NO |
|---:|---:|---:|---:|---:|---|---:|---:|
| 0.55 | 91.9% | 118,201 | 1,608 | 79.1% | [78.4%, 80.0%] | 78.7% | 79.5% |
| 0.60 | 76.7% | 98,620 | 1,608 | 83.4% | [82.6%, 84.3%] | 82.8% | 84.0% |
| 0.65 | 68.4% | 88,032 | 1,608 | 85.9% | [85.1%, 86.6%] | 85.3% | 86.3% |
| 0.70 | 60.6% | 77,923 | 1,608 | 88.2% | [87.4%, 88.9%] | 87.3% | 89.0% |
| 0.75 | 51.4% | 66,113 | 1,608 | 91.0% | [90.3%, 91.7%] | 90.3% | 91.7% |
| 0.80 | 44.7% | 57,478 | 1,608 | 92.9% | [92.3%, 93.4%] | 91.8% | 93.9% |
| 0.85 | 37.4% | 48,107 | 1,608 | 94.8% | [94.3%, 95.3%] | 94.1% | 95.5% |
| 0.90 | 29.9% | 38,422 | 1,606 | 96.6% | [96.2%, 97.0%] | 96.1% | 97.1% |
| 0.95 | 22.1% | 28,446 | 1,591 | 98.0% | [97.6%, 98.4%] | 97.8% | 98.3% |

**Sealed holdout** reproduces it almost exactly — 0.95 → 98.0% [97.4%, 98.5%] at 22.7% coverage;
0.80 → 93.0% [91.9%, 93.9%] at 45.9%. The curve is stable out of sample.

### Read this before quoting the 98%

The 98% figure is real and it is also **not** what it sounds like.

Those 28,446 selections are overwhelmingly scans where the contract is *already effectively decided*
— price far from the reference with little time left. Section 4 below shows mean |z| reaching 3.9 in
the final minute. Being right about a near-certainty is not skill, and a market that can see the
same thing will price it near $0.98.

**Selectivity buys accuracy by trading away the trades where money is made.** Whether any threshold
is *profitable* is untestable here and remains untestable until historical contract quotes exist.

---

## 4. ENTRY-TIME ANALYSIS — is later just a bigger buffer?

Phase 3 observed accuracy rising toward expiry. Section 7 asked whether that is mechanical.
**It is, and the mechanism is now measured:**

| Bucket | n | Accuracy | Clustered 95% CI | Brier | **Mean \|z\|** |
|---|---:|---:|---|---:|---:|
| 600–900s | 40,200 | 63.9% | [62.8%, 65.1%] | 0.2201 | **0.387** |
| 450–600s | 16,080 | 73.3% | [72.1%, 74.6%] | 0.1773 | **0.669** |
| 300–450s | 24,118 | 79.0% | [77.9%, 79.9%] | 0.1447 | **0.925** |
| 150–300s | 16,075 | 83.6% | [82.7%, 84.5%] | 0.1127 | **1.360** |
| 60–150s | 24,120 | 88.8% | [88.1%, 89.5%] | 0.0790 | **2.274** |
| 30–60s | 8,040 | 90.5% | [89.7%, 91.3%] | 0.0677 | **3.904** |

Accuracy and mean |z| rise in lockstep — |z| grows **ten-fold** from 0.387 to 3.904 while accuracy
rises 26.6 points. σ_remaining shrinks as √t, so an unchanged buffer mechanically becomes a larger
z-score. The model is not getting smarter late in the contract; **the question is getting easier.**

This settles section 7's question: **do not conclude "later is better."** Later is more *predictable*
and, for exactly that reason, will be more *expensive*. The two effects work against each other and
only contract prices can adjudicate — which Phase 4 does not have.

---

## 5. UNCERTAINTY — why every interval here is wide

Every interval in this report is a **window-clustered block bootstrap**, resampling contract windows
rather than rows. Two dependence layers require it:

- 16 scan points per contract share **one** label;
- Phase 3 measured 0.641 cross-asset return correlation and only **1.72 effective independent assets
  of five**.

The measured cost of honesty:

| Model | Naive 95% CI | Clustered 95% CI | Widening |
|---|---|---|---:|
| A Normal-Z | [76.49%, 76.95%] | [75.97%, 77.56%] | **3.43×** |
| B logistic | [76.48%, 76.95%] | [75.91%, 77.61%] | **3.68×** |
| D probit | [76.47%, 76.93%] | [75.89%, 77.59%] | **3.69×** |

A naive interval would have claimed ±0.23 pp precision. The honest figure is ±0.80 pp. **Reporting
the naive number would have overstated precision by more than 3×** — and would have made several of
the model differences above look decisive when they are not.

Sample sizes are large in rows and much smaller in independent units: 128,633 dev rows correspond to
1,608 windows, and after cross-asset correlation the effective count is smaller still.

---

## 6. LIMITATIONS

1. **Proxy reference.** Every label settles against the window's opening Yahoo bar, not a Webull
   contract specification. Audit UNKNOWN-3 remains open. If the venue settles on a TWAP or index,
   every label shifts.
2. **No contract economics.** No EV, no P&L, no break-even, no Kelly. The 98%-accuracy row is
   uninterpretable economically and must never be quoted as a return.
3. **28 days, one regime.** No bull/bear/shock coverage. Nothing here establishes generalisation.
4. **Effective sample is far below row count.** See section 5.
5. **Backtest features are 0–59s staler than live** (Phase 3 divergence). Conservative in direction.
6. **`buffer_velocity` was dropped**, missing at 31.25% of scan points because it needs six in-window
   bars. Dropping the feature preserved every row; dropping rows instead would have deleted the
   entire 600–900s bucket. See `PHASE4_FEATURE_ABLATION.md` §4.
7. **Holdout used exactly once**, as reported. `seal_intact: True`.
8. **Only linear models tested.** Section 2 forbids deep learning and defers gradient boosting until
   the transparent models are validated. They now are — and they lost to a closed form.
9. **Display nit:** the runner labels both holdout rows `A_normal_z` (calibrated vs raw). The
   underlying numbers are correct and distinct; the label is ambiguous and should be fixed in Phase 5
   tooling.

---

## 7. WHAT PHASE 5 SHOULD TAKE FROM THIS

1. **Normal-Z is the production probability model.** It won on the metric that matters and it cannot
   overfit.
2. **Do not add features hoping for gains.** 60 features produced a *worse* model than one. The
   signal is concentrated in buffer-over-sigma.
3. **The geometry set is the one live lead** — retest it with a nonlinear model that can bend around
   Φ, rather than a linear index that must imitate it.
4. **Any gain must clear the clustered interval**, not the naive one.
5. **Get contract prices.** Everything about *value* is still unanswerable, and the selective-accuracy
   curve is the sharpest illustration yet of why accuracy alone cannot pick a threshold.
