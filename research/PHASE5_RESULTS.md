# MANTIS — PHASE 5 RESULTS

```
PROXY SETTLEMENT REFERENCE
NO HISTORICAL WEBULL CONTRACT QUOTES
CLASSIFICATION RESEARCH ONLY
NOT A PROFITABILITY BACKTEST
LIMITED RECENT HISTORICAL REGIME
```

## Headline

No challenger robustly beat Normal-Z on out-of-sample Brier score. Normal-Z remains the probability anchor.

The chronological holdout remained sealed and was not used for Phase 5 tuning.
NO TRADE remains an abstention, not an incorrect prediction.

## Model results

| Model | n | Brier | Log loss | Slope | ECE | Accuracy | Clustered 95% CI |
|---|---:|---:|---:|---:|---:|---:|---|
| gaussian | 128633 | 0.15132 | 0.45711 | 0.965 | 0.0126 | 76.37% | [75.63%, 77.13%] |
| student_t | 128633 | 0.15155 | 0.45548 | 0.881 | 0.0204 | 76.37% | [75.63%, 77.13%] |
| empirical | 128633 | 0.15221 | 0.45699 | 0.912 | 0.0175 | 76.37% | [75.63%, 77.13%] |
| bootstrap | 128633 | 0.15277 | 0.45814 | 0.901 | 0.0198 | 76.25% | [75.56%, 76.97%] |
| monte_carlo | 40000 | 0.15016 | 0.45463 | 0.971 | 0.0092 | 77.22% | [76.41%, 78.02%] |

### Paired Brier comparisons against Normal-Z

| Challenger | n | ΔBrier | Clustered 95% CI | Verdict |
|---|---:|---:|---|---|
| student_t | 128633 | +0.000229 | [-0.000163, +0.000602] | NOT DISTINGUISHABLE |
| empirical | 128633 | +0.000884 | [+0.000451, +0.001350] | WORSE |
| bootstrap | 128633 | +0.001449 | [+0.000922, +0.001989] | WORSE |
| monte_carlo | 40000 | -0.000063 | [-0.000176, +0.000042] | NOT DISTINGUISHABLE |

## Monte Carlo, terminal distributions, and uncertainty

Monte Carlo configuration/results: `{"n": 40000, "paths": 2000, "probability_standard_error": {"q05": 0.0004998749843710925, "q50": 0.00952249967182987, "q95": 0.011175956334918278, "q100": 0.011180339887498949}, "p_cross_reference": {"q05": 0.001, "q50": 0.388, "q95": 0.8705}, "gaussian_p_cross_reference": {"q05": 2.011978990614549e-05, "q50": 0.49372939061681237, "q95": 0.9928843458760483}, "crossing_difference_mc_minus_gaussian": {"q05": -0.1765544664314116, "q50": -0.08161463145631376, "q95": 0.0013265062833938443}, "q05_terminal_return": {"q05": -0.005245082629999403, "q50": -0.0017043968522826858, "q95": -0.0004428576982217159}, "q50_terminal_return": {"q05": -6.762885104212857e-05, "q50": -1.4530416181934847e-06, "q95": 6.421040134512154e-05}, "q95_terminal_return": {"q05": 0.000442685488335548, "q50": 0.0017093817521594001, "q95": 0.005253183625610877}, "expected_terminal_buffer": {"q05": -0.0024591335848792783, "q50": -4.167925254830521e-06, "q95": 0.0025451392977774777}}`

Student-t terminal diagnostics: `{"nu": 4.456245976114701, "n": 128633, "q05_terminal_return": {"q05": -0.004914346225606714, "q50": -0.0016153535321344892, "q95": -0.0004278552431056904}, "q50_terminal_return": {"q05": 0.0, "q50": 0.0, "q95": 0.0}, "q95_terminal_return": {"q05": 0.0004278552431058014, "q50": 0.0016153535321343782, "q95": 0.004914346225606669}}`

Conservative-bound slices: `{"confidence>=0.80": {"n": 55155, "point_confidence": 0.9363294278379558, "mean_lower_bound": 0.9184466338570386, "mean_shortfall": 0.01788279398091719, "lower_bound_quantiles": {"q05": 0.8000539038457112, "q50": 0.9340156370980434, "q95": 0.9967606385276258}}, "confidence>=0.85": {"n": 46751, "point_confidence": 0.9563572533328324, "mean_lower_bound": 0.9381603440046027, "mean_shortfall": 0.018196909328229705, "lower_bound_quantiles": {"q05": 0.8439972646940423, "q50": 0.9519053040600749, "q95": 0.9967606385276258}}, "confidence>=0.90": {"n": 38324, "point_confidence": 0.9742545364011339, "mean_lower_bound": 0.9560884042974956, "mean_shortfall": 0.018166132103638266, "lower_bound_quantiles": {"q05": 0.883426198253582, "q50": 0.9672414715878378, "q95": 0.9967717287387636}}, "confidence>=0.95": {"n": 29374, "point_confidence": 0.9891301909227801, "mean_lower_bound": 0.9720706441436593, "mean_shortfall": 0.017059546779120804, "lower_bound_quantiles": {"q05": 0.9217005373463231, "q50": 0.9798820093102818, "q95": 0.9977949078923106}}, "fragility=LOW": {"n": 12160, "point_confidence": 0.999451148154901, "mean_lower_bound": 0.9923738680495892, "mean_shortfall": 0.007077280105311834, "lower_bound_quantiles": {"q05": 0.9800390955068964, "q50": 0.9939589971819816, "q95": 0.9985517375666428}}, "fragility=MEDIUM": {"n": 68443, "point_confidence": 0.7119862906271284, "mean_lower_bound": 0.6901318891514657, "mean_shortfall": 0.021854401475662644, "lower_bound_quantiles": {"q05": 0.466093248356918, "q50": 0.6372097796166836, "q95": 0.9791876615651567}}, "fragility=HIGH": {"n": 47998, "point_confidence": 0.7755283459295783, "mean_lower_bound": 0.750969051732203, "mean_shortfall": 0.024559294197375348, "lower_bound_quantiles": {"q05": 0.545856209168552, "q50": 0.7551558600351057, "q95": 0.9358325257068166}}, "fragility=EXTREME": {"n": 32, "point_confidence": 0.6730827766006627, "mean_lower_bound": 0.6195458110716425, "mean_shortfall": 0.05353696552902021, "lower_bound_quantiles": {"q05": 0.5338265694204605, "q50": 0.6292908311356895, "q95": 0.7031882411792029}}}`

## Heavy tails and volatility

Normality verdict: **moderately heavy tails — up to 1.8x Gaussian**

Student-t fit: `{"status": "OK", "n": 32080, "df": 4.456245976114701, "loc": 0.032251943080432985, "scale": 0.7869311751761089, "interpretation": "heavy tailed"}`

Volatility estimators and paired comparisons are recorded in the JSON output under `volatility` and `volatility_vs_validated`.

## Fragility, disagreement, and dependence

Fragility results: `{"bands": {"LOW": {"n": 12160, "share": 0.09453250721043589, "accuracy": 0.9942434210526315, "ci": [0.9912039801265656, 0.9966676976087464], "brier": 0.005723764673062187}, "MEDIUM": {"n": 68443, "share": 0.5320796374180808, "accuracy": 0.711438715427436, "ci": [0.7017278913847088, 0.7209580349485827], "brier": 0.17193971834597901}, "HIGH": {"n": 47998, "share": 0.3731390856156663, "accuracy": 0.7799283303470977, "ci": [0.7697052838040813, 0.7895437562756221], "brier": 0.1587288857299494}, "EXTREME": {"n": 32, "share": 0.0002487697558169366, "accuracy": 0.5, "ci": [0.33306451612903226, 0.6774193548387096], "brier": 0.27056491569694674}}, "summary": {"mean": 44.75666397279563, "median": 46.112490947274196, "bands": {"LOW": 12160, "MEDIUM": 68443, "HIGH": 47998, "EXTREME": 32}}, "weights": {"gamma": 0.2, "vega": 0.15, "theta": 0.1, "proximity": 0.2, "time": 0.1, "crossings": 0.1, "vol_of_vol": 0.05, "disagreement": 0.1}}`

Disagreement study: `{"status": "OK", "confidence_floor": 0.8, "n": 55155, "n_windows": 1608, "overall_accuracy": 0.9341129544012329, "mean_disagreement_winners": 0.025938265718597177, "mean_disagreement_losers": 0.04302211335171248, "disagreement_gap": 0.017083847633115302, "buckets": [{"quantile": 1, "disagreement_low": 0.0002523977788976284, "disagreement_high": 0.007776786814110703, "n": 13789, "n_windows": 1370, "accuracy": 0.9908622815287548, "ci_low": 0.9872299049265342, "ci_high": 0.9937816745108069}, {"quantile": 2, "disagreement_low": 0.007776786814110703, "disagreement_high": 0.02275743573997646, "n": 13788, "n_windows": 1567, "accuracy": 0.9670002901073397, "ci_low": 0.9603093485727304, "ci_high": 0.9726760327524028}, {"quantile": 3, "disagreement_low": 0.02275743573997646, "disagreement_high": 0.04190644433676294, "n": 13789, "n_windows": 1604, "accuracy": 0.9121763724708101, "ci_low": 0.90323186466161, "ci_high": 0.9215196083629663}, {"quantile": 4, "disagreement_low": 0.04190644433676294, "disagreement_high": 0.12297650024952644, "n": 13789, "n_windows": 1597, "accuracy": 0.8664152585394155, "ci_low": 0.8555591742689619, "ci_high": 0.8782356685421989}], "accuracy_drop_low_to_high": 0.1244470229893393, "separates": true}`

Cross-asset dependence: `{"n_windows": 1608, "assets": ["ADA-USD", "BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD"], "correlation_matrix": {"ADA-USD": {"ADA-USD": 1.0, "BTC-USD": 0.4626359210782998, "ETH-USD": 0.48066252361458467, "SOL-USD": 0.5192028229347098, "XRP-USD": 0.5298098875210191}, "BTC-USD": {"ADA-USD": 0.4626359210782998, "BTC-USD": 1.0, "ETH-USD": 0.8614672983953342, "SOL-USD": 0.7935835216478531, "XRP-USD": 0.7305247384047673}, "ETH-USD": {"ADA-USD": 0.48066252361458467, "BTC-USD": 0.8614672983953342, "ETH-USD": 1.0, "SOL-USD": 0.8507414219715419, "XRP-USD": 0.7771801480057283}, "SOL-USD": {"ADA-USD": 0.5192028229347098, "BTC-USD": 0.7935835216478531, "ETH-USD": 0.8507414219715419, "SOL-USD": 1.0, "XRP-USD": 0.7899908180735954}, "XRP-USD": {"ADA-USD": 0.5298098875210191, "BTC-USD": 0.7305247384047673, "ETH-USD": 0.7771801480057283, "SOL-USD": 0.7899908180735954, "XRP-USD": 1.0}}, "mean_pairwise_correlation": 0.6795799101647433, "median_pairwise_correlation": 0.7538524432052478, "effective_independent_assets": 1.7000416363302018, "mean_same_direction_share": 0.8327114427860697, "interpretation": "Same-window assets are materially dependent; rows are not independent bets and uncertainty must remain window-clustered."}`

## Components that survive into Phase 6

- Normal-Z: SURVIVES as the probability anchor; it is not altered by fragility.
- Student-t: retain only as a heavy-tail diagnostic unless its paired clustered Brier interval beats Normal-Z.
- Empirical conditional distribution and empirical bootstrap: retain as diagnostic challengers, not production replacements without robust OOS improvement.
- Monte Carlo: retain for terminal quantiles, sampling error, and path-dependent crossing diagnostics; not as directional alpha.
- Digital sensitivities (d2/delta/gamma/vega/theta): retain as diagnostics only.
- Fragility and disagreement: retain as abstention research diagnostics; do not shade calibrated probability with either score.
- Conservative lower bounds: retain as displayed uncertainty diagnostics where sampling error/model dispersion is defensible.
- Validated volatility estimator: retain unless a challenger beats it under paired window-clustered Brier comparison.
- Same-window grouping and cross-asset dependence adjustment: mandatory for all later uncertainty estimates.

## Limitations

- PROXY SETTLEMENT REFERENCE: labels use the first qualifying underlying bar, not a verified venue settlement reference.
- NO HISTORICAL WEBULL CONTRACT QUOTES: market-implied probability, spread, and execution cost cannot be reconstructed.
- CLASSIFICATION RESEARCH ONLY.
- NOT A PROFITABILITY BACKTEST: no EV, P&L, break-even, fee, slippage, or return claim is supported.
- LIMITED RECENT HISTORICAL REGIME: the sample does not establish robustness across bull, bear, shock, or illiquid regimes.
- The untouched chronological holdout is not used in Phase 5 tuning or model selection.
- Multiple scan rows share one contract outcome; effective sample size is far below row count.
- BTC/ETH/SOL/ADA/XRP within the same 15-minute window are strongly dependent and cannot be counted as independent bets.
- Yahoo one-minute bars are coarse and may differ from live or venue-index observations.
- Monte Carlo standard error measures finite-path sampling error only, not model misspecification.
- Gaussian crossing probability relies on a driftless continuous-path approximation; empirical 15-second MC crossing is grid-dependent.
- Empirical/bootstrap channels assume the limited recent training regime is informative about evaluation states.
- Fragility weights are declared diagnostics, not outcome-fitted probabilities; selective improvements may partly reflect mechanically easier late-window states.
- NO TRADE is an abstention, not an incorrect prediction.

Phase 5 stops here. Phase 6 requires explicit approval.
