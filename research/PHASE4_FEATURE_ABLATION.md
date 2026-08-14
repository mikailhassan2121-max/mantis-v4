# MANTIS — PHASE 4 FEATURE ABLATION

```
PROXY SETTLEMENT REFERENCE
NO HISTORICAL WEBULL CONTRACT QUOTES
CLASSIFICATION RESEARCH ONLY — NOT A PROFITABILITY BACKTEST
LIMITED RECENT HISTORICAL REGIME
```

**Method:** L2-regularized logistic, window-grouped chronological walk-forward on the development set
(4 folds, 128,633 out-of-sample rows / 1,608 windows). Per-fold calibration. Sealed holdout untouched.

---

## 1. RESULTS

| Feature set | k | Brier | Log loss | Slope | ECE | Accuracy |
|---|---:|---:|---:|---:|---:|---:|
| **normal_z_only** | **1** | 0.1512 | 0.4564 | 0.962 | 0.0066 | 76.72% |
| geometry | 10 | **0.1509** | **0.4534** | 0.948 | 0.0076 | **77.05%** |
| geometry + momentum | 21 | **0.1509** | 0.4531 | 0.944 | 0.0069 | 77.02% |
| geometry + volatility | 20 | 0.1514 | 0.4545 | 0.949 | 0.0059 | 76.91% |
| geometry + structure | 29 | 0.1517 | 0.4550 | 0.994 | 0.0052 | 76.93% |
| geometry + cross-asset | 16 | 0.1512 | 0.4539 | 0.945 | 0.0072 | 76.95% |
| geometry + time | 14 | 0.1520 | 0.4561 | 0.944 | 0.0066 | 76.77% |
| all_features | 60 | 0.1528 | 0.4576 | 0.984 | 0.0065 | 76.72% |

### The shape of this table is the finding

**Brier gets worse as features are added.** Best at 10 features (0.1509), worst at 60 (0.1528). Every
family added on top of geometry either fails to help or actively hurts.

A single feature — `normal_z` — is within 0.0003 Brier of the best set in the table. **One number
carries essentially all of the signal.**

---

## 2. FAMILY-BY-FAMILY VERDICTS

Judged as the incremental effect over `geometry` (Brier 0.1509, log loss 0.4534, accuracy 77.05%):

| Family | ΔBrier | ΔLog loss | ΔAccuracy | Verdict |
|---|---:|---:|---:|---|
| **momentum** | 0.0000 | −0.0003 | −0.03 pp | **no value** — noise-level |
| **volatility** | +0.0005 | +0.0011 | −0.14 pp | **harmful** |
| **structure** | +0.0008 | +0.0016 | −0.12 pp | **harmful** (worst family) |
| **cross-asset** | +0.0003 | +0.0005 | −0.10 pp | **no value** |
| **time** | +0.0011 | +0.0027 | −0.28 pp | **harmful** |

**Every family is neutral or harmful.** Not one earns its place. Per Phase 4 section 4 — *"If a
feature family does not improve robust OOS performance, remove it"* — all five are removed.

### On trend indicators specifically

Section 4 asked whether trend indicators remain harmful after proper fitting. Phase 3 showed V3's
hand-tuned trend adjustment was actively damaging (78.6% of V3's trades were trend-dependent and
scored 71.7% vs 84.4% for buffer-sufficient trades).

**Properly fitted, trend indicators are no longer harmful — they are merely useless.** The `structure`
family (EMA stack, EMA slopes, MACD, RSI, reference-crossing statistics — 19 features) makes Brier
*worse* by 0.0008 and accuracy worse by 0.12 pp. `momentum` (11 features) is a dead heat.

So the Phase 3 conclusion is refined rather than overturned: V3's specific hand-tuned *transformation*
was harmful; regression removes that harm by shrinking the coefficients toward zero, but there is no
trend signal underneath to recover. **V3's trend term must not be reinstated on any grounds.**

### On cross-asset features

This one deserves comment because Phase 3 made a strong case for it. Cross-asset correlation is real
and large (0.641 return correlation, 1.72 effective independent assets of five), and it absolutely
matters for **uncertainty quantification** and **position sizing**.

It does **not** follow that cross-asset features improve a *per-contract probability*. They do not
here (ΔBrier +0.0003). The correlation tells you your five bets are one bet; it does not tell you
which way that bet resolves. Those are different questions, and Phase 3's finding stands for the
first while this ablation answers the second.

---

## 3. WHY MORE FEATURES MADE IT WORSE

Three effects, all pointing the same way:

1. **The functional form is already right.** Φ(buffer/σ) is the closed form for a driftless random
   walk. A linear-in-features logit cannot represent it exactly, so extra features are spent
   approximating a shape Normal-Z has for free.
2. **Estimation variance.** 60 coefficients estimated on ~1,600 *effective* independent windows —
   not 128,633 rows. The effective-sample-to-parameter ratio is far worse than the row count
   suggests.
3. **Redundancy.** `normal_z`, `buffer_over_sigma`, `buffer_pct`, `buffer_bps` and `buffer_abs` are
   near-collinear by construction; adding correlated families inflates variance without adding
   information.

---

## 4. A CLEANING BUG THAT WOULD HAVE INVALIDATED SECTION 7

Worth recording because it was caught by verification, not by tests, and because its failure mode was
silent.

`buffer_velocity` (buffer now minus buffer five bars ago) requires six bars inside the window. At the
five earliest scan offsets (840, 780, 720, 660, 600 seconds remaining) only 1–5 bars exist, so it is
**structurally undefined** — missing at exactly 5/16 = **31.25%** of rows, matching the measured
31.25% precisely.

The original cleaning dropped **rows** to satisfy every feature. Measured consequence on real data:

| | Rows kept | Mean seconds_remaining | 600–900s bucket |
|---|---:|---:|---:|
| Drop rows (original) | 36,465 of 53,040 | **256.4** | **0 rows — entirely deleted** |
| Drop feature (fixed) | 53,040 of 53,040 | **401.2** | fully populated |

It deleted **100% of scans at the five earliest offsets**. The entry-time analysis in section 7 would
have been computed on a dataset containing **no early entries at all**, while appearing to work.

**Fix:** drop the *feature*, keep the *rows*. At full scale this retains 214,313 of 214,320 rows
(7 dropped, not 66,000). Outcome base rate is unchanged to 6 decimal places, so the fix introduces no
label bias.

Guarded by three regression tests
(`test_structurally_missing_feature_is_dropped_not_the_rows`,
`test_row_dropping_alone_would_bias_entry_time`,
`test_outcome_base_rate_survives_cleaning`) plus a `run_walk_forward` guard that names any offending
feature instead of surfacing an opaque sklearn `NaN` error several frames deep.

---

## 5. RETAINED FEATURE SET

**Production (Phase 4 outcome): `normal_z` alone**, via Normal-Z — one derived quantity, zero fitted
parameters.

The 10-feature `geometry` set is retained as the **only** live lead for Phase 5:

```
spot, reference, buffer_abs, buffer_pct, buffer_bps,
seconds_remaining, elapsed_fraction, sigma_remaining_return,
buffer_over_sigma, normal_z
```

It showed a distinguishable log-loss and accuracy edge on dev folds (+0.32 pp accuracy) but **failed
to distinguish on Brier on both dev and the sealed holdout** (holdout ΔBrier −0.00066, 95% CI
[−0.00133, +0.00004]). That is not a robust improvement. See `PHASE4_MODEL_RESULTS.md` §2.

**Dropped:** momentum (11), volatility (10), structure (19), cross-asset (6), time (4) —
50 features removed, and `buffer_velocity` removed for structural missingness.

---

## 6. LIMITATIONS

1. **Ablation used one model class.** L2 logistic. A family that is useless to a linear index may
   carry signal a nonlinear model could use — the strongest argument for Phase 5 revisiting
   `geometry` and `structure` with trees.
2. **Families were tested additively, not exhaustively.** Interactions between two dropped families
   are untested; with 60 features the full lattice is impractical and would invite overfitting.
3. **Feature selection used development folds only.** Correct procedure, and it means these
   comparisons carry fold-level noise — the differences here are small relative to the clustered
   intervals in the model results report.
4. **28 days, one regime.** A family that is useless in a calm month may matter in a shock.
   Volatility and structure features are exactly the ones that would plausibly earn their place in a
   regime this dataset does not contain.
5. **Proxy labels.** Ablation is against proxy-settled outcomes, not venue settlements.
6. **No economic ablation is possible.** A feature that improves *EV* without improving classification
   accuracy would be invisible to every table here.
