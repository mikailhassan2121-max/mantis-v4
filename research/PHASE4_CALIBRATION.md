# MANTIS — PHASE 4 CALIBRATION

```
PROXY SETTLEMENT REFERENCE
NO HISTORICAL WEBULL CONTRACT QUOTES
CLASSIFICATION RESEARCH ONLY — NOT A PROFITABILITY BACKTEST
LIMITED RECENT HISTORICAL REGIME
```

**The question Phase 4 exists to answer:** when MANTIS says an outcome has probability *p*, how close
is that to the observed frequency out of sample?

**The answer, on the sealed holdout: close.** Normal-Z is well calibrated without correction —
calibration slope **1.011**, intercept **0.010**, ECE **0.0098**, max bucket error **0.0296**.

That matters more than any accuracy figure in this project, because every future EV calculation
consumes the probability directly. A miscalibrated 90% produces a confidently wrong expected value.

---

## 1. HOLDOUT RELIABILITY — the table that answers the question

Normal-Z, isotonic-calibrated, 53,600 rows / 670 windows, **evaluated once**.
Probabilities are folded to confidence, so a "90% NO" sits alongside a "90% YES" — what matters is
how often the side MANTIS would take actually wins.

| Bucket | n | Predicted | **Observed** | 95% CI | Gap |
|---|---:|---:|---:|---|---:|
| 0.50–0.55 | 2,861 | 52.6% | 51.0% | [49.2%, 52.8%] | +1.6 pp |
| 0.55–0.60 | 9,049 | 56.8% | 57.7% | [56.7%, 58.7%] | −0.9 pp |
| 0.60–0.65 | 3,569 | 61.4% | 64.0% | [62.4%, 65.6%] | −2.6 pp |
| 0.65–0.70 | 5,108 | 66.6% | 69.6% | [68.3%, 70.8%] | −3.0 pp |
| 0.70–0.75 | 4,644 | 72.8% | 73.1% | [71.8%, 74.3%] | −0.3 pp |
| 0.75–0.80 | 3,747 | 77.6% | 78.5% | [77.1%, 79.7%] | −0.9 pp |
| 0.80–0.85 | 3,084 | 82.2% | 83.7% | [82.3%, 85.0%] | −1.5 pp |
| 0.85–0.90 | 4,931 | 87.5% | 87.4% | [86.4%, 88.3%] | +0.1 pp |
| 0.90–0.95 | 4,425 | 92.8% | 92.0% | [91.2%, 92.8%] | +0.8 pp |
| 0.95–1.00 | **12,182** | 98.2% | **98.0%** | [97.8%, 98.2%] | +0.2 pp |

**Reading it:**

- **No bucket is materially overconfident.** The largest positive gap is +1.6 pp in the 0.50–0.55
  bucket, where the model is barely committing anyway.
- **The high-confidence buckets are the best behaved.** 0.95+ predicts 98.2% and delivers 98.0% on
  12,182 predictions. 0.85–0.90 predicts 87.5% and delivers 87.4%. This is the regime that matters
  for a selective system, and it holds.
- **The mid-range is mildly *under*confident.** 0.60–0.70 wins about 3 points more often than
  advertised. Erring toward understatement is the safe direction — but it is still a miss, and it
  means EV in that band would be slightly *understated*, not overstated.

The Phase 4 brief said: *"If a model predicts 85% but historically wins 72%, that must be exposed
clearly."* Nothing like that appears. The largest miss in any bucket above 0.60 is 3.0 pp, in the
conservative direction.

---

## 2. DID CALIBRATION HELP?

Barely — because there was little to fix.

| | Brier | Log loss | Slope | ECE |
|---|---:|---:|---:|---:|
| Normal-Z **raw** (holdout) | 0.1478 | 0.4486 | 0.986 | 0.0194 |
| Normal-Z **isotonic** (holdout) | 0.1474 | 0.4447 | **1.011** | **0.0098** |

ΔBrier = **−0.00046**, 95% CI **[−0.00112, +0.00021]** → **not distinguishable**.

Isotonic halves ECE (0.0194 → 0.0098) and moves the slope from 0.986 to 1.011, but the improvement
in Brier is inside the noise. **The honest statement is that Normal-Z arrives calibrated and
recalibration is cosmetic on this data.**

That is a genuinely favourable property: a closed-form probability that is already calibrated needs
no fitted correction layer that could itself drift or overfit.

### Which calibrator won, per fold

| Model | Fold votes |
|---|---|
| A Normal-Z | uncalibrated ×2, isotonic ×2 |
| B logistic | Platt ×2, uncalibrated ×2 |
| C logistic L2 | Platt ×2, uncalibrated ×2 |
| C logistic L1 | Platt ×2, uncalibrated ×2 |
| D probit | Platt ×2, uncalibrated ×2 |

**No calibrator won a majority anywhere.** Every model splits 2–2 between "apply a correction" and
"leave it alone" — exactly the pattern expected when the raw probabilities are already close to
right and the selection is being driven by fold-level noise.

Notice also that the fitted models needed Platt (a slope/intercept fix) while Normal-Z alternated
between isotonic and nothing. The fitted models were *systematically* off in a way a two-parameter
correction repairs; Normal-Z was not systematically off at all.

---

## 3. DEVELOPMENT-FOLD CALIBRATION (all models)

Out-of-sample across 4 walk-forward folds, 128,633 rows, after per-fold calibration:

| Model | Brier | Log loss | Slope | Intercept | ECE | MCE | Verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| A Normal-Z | **0.1512** | **0.4564** | 0.962 | −0.074 | 0.0066 | 0.0243 | reasonably calibrated |
| B logistic | 0.1528 | 0.4576 | 0.983 | +0.058 | 0.0065 | 0.0167 | reasonably calibrated |
| C logistic L2 | 0.1528 | 0.4576 | 0.984 | +0.059 | 0.0065 | 0.0160 | reasonably calibrated |
| C logistic L1 | 0.1528 | 0.4575 | 0.984 | +0.054 | 0.0060 | 0.0154 | reasonably calibrated |
| D probit | 0.1531 | 0.4593 | 0.985 | +0.060 | **0.0049** | **0.0103** | reasonably calibrated |

An honest observation that cuts against the headline: **probit has the best ECE and MCE** (0.0049 /
0.0103) while having the worst Brier. Calibration and discrimination are different properties. Probit
spreads its probabilities more faithfully but separates the classes slightly less well. Since Phase 4
was pre-committed to Brier/log loss as the selection criteria, probit does not win — but it is worth
recording that "best calibrated" and "best model" were not the same row.

Raw (uncalibrated) slopes for the fitted models sat near 0.90, i.e. mildly **overconfident**;
calibration moved them to ≈0.98. Normal-Z's raw slope was already 0.965.

---

## 4. HOW CALIBRATION WAS KEPT HONEST

Section 5 requires the calibrator be fitted and selected without touching what it is scored on.
Enforced structurally:

```
training block  ->  [ model-fit portion | calibration portion ]
                                          last 25% of windows, chronological
                    fit calibrators on one half of the calibration block,
                    SELECT using the other half,
                    refit the winner on the full calibration block,
                    apply to the fold's test block (unseen by both)
```

- `select_calibrator()` takes **fit** and **selection** arrays as separate arguments, so passing the
  same rows to both is a visible act rather than an accident.
- The internal split is **window-grouped and chronological**, so no contract straddles it and no
  same-window cross-asset pair is split — verified by
  `test_calibration_split_is_grouped_and_chronological`.
- Calibrating on the model's *own training rows* would be worse than not calibrating: training
  predictions are systematically sharper than out-of-sample ones, so the correction would be fitted
  in the wrong direction.
- The sealed holdout was never used for calibrator selection. `seal_intact: True`.

---

## 5. LIMITATIONS SPECIFIC TO THESE CALIBRATION RESULTS

1. **Calibration is against a proxy label.** These probabilities are calibrated to
   "terminal Yahoo price above the window's opening Yahoo price". If Webull settles differently,
   calibration against the *real* contract is unmeasured.
2. **One regime, 28 days.** Calibration is a property of a model *and its environment*. A volatility
   regime change can decalibrate a model that looks perfect here. Phase 17's live monitor exists for
   exactly this.
3. **Bucket counts are rows, not independent observations.** The 0.95+ bucket's 12,182 rows come from
   far fewer independent windows; its interval is correspondingly optimistic. The clustered intervals
   in the model results report are the honest ones.
4. **The 0.50–0.55 bucket is the weakest** (+1.6 pp overconfident). It is also the band a selective
   system would never trade.
5. **ECE depends on bucket edges.** The ten buckets are the ones Phase 4 section 5 specified; a
   different partition would give a slightly different ECE.

---

## 6. BOTTOM LINE

**Normal-Z's stated probabilities can be believed, within about ±3 percentage points, on this
dataset and this regime.** High-confidence predictions are the most trustworthy part of the range.

That is the precondition for everything economic that follows: once contract prices exist, EV can be
computed from these probabilities without a correction layer standing between the model and the
decision. It does **not** mean any trade is profitable — only that the probability going into that
calculation is not lying.
