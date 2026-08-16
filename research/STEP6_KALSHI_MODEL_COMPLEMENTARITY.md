# Step 6 — Kalshi model complementarity

Status: `RESEARCH_SHADOW_ONLY`
Policy: `KALSHI_COMPLEMENTARITY_V1`

## Frozen inputs and discipline

This study compares the frozen Step 4
`TERMINAL_PRICE_CONSERVATIVE_PROXY_NO_SQRT60` probability under
`KALSHI_REFERENCE_V1` with the frozen Step 5
`KALSHI_PROBABILITY_RESEARCH_V1` HGB + Platt model. It neither retrains nor
recalibrates either model. The runner verifies the Step 5 model, schema, data,
and configuration hashes before analysis, reproduces the chronological shared-
window splits, and requires the original holdout cohort of 1,377 entries from
2,111 eligible contracts (65.2297%).

Development is used for regime thresholds and the auxiliary diagnostic model.
Validation checks persistence. The old Step 5 holdout is labelled
`PREVIOUSLY_OPENED_HOLDOUT_NOT_STEP6_SEALED`; it is descriptive, not a new
sealed test. There are no reconstructed rows after its final timestamp, so
`NEW_STEP6_SEALED_SAMPLE = INSUFFICIENT_DATA`.

## Predeclared analyses

The report contains four direction cells, fixed absolute-disagreement buckets,
fixed high-confidence agreement thresholds, fixed skepticism/rescue definitions,
calibration conditional on agreement, per-asset and side results, and regimes
for time remaining, volatility, absolute Normal-Z target distance, target
crossings, and Yahoo/Kalshi reference gap. Volatility and reference-gap terciles
are derived only from development and frozen for validation/old-holdout use.
Intervals use quarter-hour-window cluster bootstrap; state rows are never
presented as independent trials.

An auxiliary logistic failure analysis is diagnostic only. It is fit on
development and scored on validation. It is not an ensemble, veto, selector,
or trading model.

## Principal result

On validation, model directions agree on 95.30% of all states and disagree on
4.70% (1,582 states). In disagreements, ML is correct 54.42% versus 45.58% for
the statistical model. The previously opened holdout shows the same direction:
94.29% agreement; in 1,928 disagreements ML is correct 53.68% versus 46.32%.
Errors remain highly correlated (validation 0.854; old holdout 0.825), so the
models are far from independent.

On the fixed Step 4 qualified-entry cohort, directions agree in every row in
development, validation, and the old holdout. Consequently, Step 6 finds no
ML veto/rescue direction signal for the existing qualified cohort. Validation
accuracy remains 98.67%; the old opened holdout remains 97.39%. No gate was
changed.

Disagreement is concentrated near the target and in earlier/lower-volatility,
more frequently crossing states. Increasing raw probability disagreement does
not produce a monotonic failure curve: validation statistical failure rates are
9.13%, 21.10%, 25.55%, 27.71%, 23.27%, and 19.67% across the six fixed buckets.
It is therefore not a standalone risk rule.

High-confidence same-direction agreement is reliable but not independently
actionable: validation accuracy is 98.00% at both >=.90 (n=10,466), 98.76% at
both >=.95 (n=8,067), 99.39% at both >=.975 (n=6,191), and 99.71% at both >=.99
(n=4,823). These nested subsets reflect confidence/distance as well as agreement.

The predeclared statistical-confident/ML-skeptical cells are essentially empty
on validation (D1 n=0, D2 n=3, D3 n=0), and empty in the fixed entry cohort.
They cannot support a veto hypothesis. ML-confident/statistical-skeptical cells
exist (E1 n=557, E2 n=645), but both models choose the same direction in those
definitions; they describe ML confidence refinement, not rescued trades.

The development-fitted auxiliary diagnostic improves validation failure Brier
from 0.136989 to 0.136159 and log loss from 0.418152 to 0.416233 after adding
ML confidence and disagreement. That is modest incremental descriptive
information, not an independently validated rule.

## Asset and regime observations

Validation all-state statistical/ML accuracy is BTC 80.29/80.79%, ETH
79.70/79.48%, SOL 80.49/80.69%, and XRP 77.69/78.88%. The improvement is not
uniform across assets. Disagreement is most common near the target (<0.5Z),
where validation accuracy is 61.6/62.7%, and disappears beyond 1Z in this
cohort. Exactly-one-correct rates decline from early to late states and rise
with crossing count. Reference-gap terciles show no simple monotonic failure
relationship. These are descriptive relationships, not new gates.

## Future hypotheses (unvalidated)

`STEP6_FUTURE_HYPOTHESES`:

- Prospectively test whether ML has a small advantage in near-target direction
  disagreements.
- Prospectively test whether shared high confidence adds value beyond either
  model's confidence alone.
- Collect a genuinely new post-Step-5 cohort before considering confirmation,
  veto, stacking, weighting, or dynamic switching.

None is implemented.

## Limitations

- Yahoo current value remains a proxy for CF Benchmarks.
- The Kalshi ending 60-second benchmark average remains approximated.
- The Step 5 holdout was already opened before Step 6.
- No new post-holdout sealed sample exists.
- History spans a limited recent regime.
- Repeated states from contracts/windows are dependent.
- Sparse skepticism and extreme-disagreement buckets cannot support precise
  conclusions.
- Transferred Step 4 gates remain unvalidated for this reference policy.

## Decision

`COMPLEMENTARITY_SUGGESTED_REQUIRES_FORWARD_VALIDATION`
