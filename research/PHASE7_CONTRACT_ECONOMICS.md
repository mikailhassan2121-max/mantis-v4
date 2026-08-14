# Phase 7 Contract Economics

> **PROXY SETTLEMENT REFERENCE**  
> **NO HISTORICAL WEBULL CONTRACT QUOTES**  
> **CLASSIFICATION RESEARCH ONLY**  
> **NOT A PROFITABILITY BACKTEST**  
> **LIMITED RECENT HISTORICAL REGIME**

Phase 7 adds an economics layer after, and never inside, the locked Phase 6 policy `H_p0.95_l0.90_f50_d.05_t300`.

## Exact formulas

For payout `R`, executable ask `A`, model win probability `p`, and known per-contract acquisition costs `C = fees + slippage`:

```text
gross EV             = pR - A
net EV               = pR - A - C
break-even p (gross) = A / R
break-even p (net)   = (A + C) / R
model edge           = p - break-even p
expected return/cost = EV / (A + C)       [A when costs are unknown]
max loss             = A + C
max payout           = R
```

The equivalent outcome expansion is `p(R-A-C) + (1-p)(-A-C)`. BUY YES uses YES ASK; BUY NO uses NO ASK. Midpoint and last trade are never entry economics.

If fees or slippage are not defensible, net EV is `N/A`, not zero-cost EV. The displayed value is `EV_BEFORE_UNVERIFIED_FEES`.

## Status logic

- `ROBUST_POSITIVE_EV`: point and lower-bound EV are positive and the LCB edge clears the marginal band.
- `MARGINAL_EV`: positive, but the economic margin is small.
- `NON_ROBUST_EV`: point EV is positive but LCB EV is not.
- `NEGATIVE_EV`: point EV is zero or negative.
- `ECONOMICS_UNAVAILABLE`: no verified contract economics.
- `INVALID_OR_STALE_QUOTE`: identity, time, price, reference, or book validation failed.
- `ECONOMICS_UNVERIFIED`: source verification failed.

## Deterministic synthetic formula cases

These are unit-test fixtures, never historical performance observations.

| Model p | Ask | Example LCB | Point gross EV | LCB gross EV | Interpretation |
|---:|---:|---:|---:|---:|---|
| 0.95 | 0.60 | 0.90 | +0.35 | +0.30 | robust positive before unverified fees |
| 0.95 | 0.94 | 0.94 | +0.01 | 0.00 | marginal / boundary |
| 0.95 | 0.94 | 0.90 | +0.01 | -0.04 | point-positive, LCB-negative |
| 0.95 | 0.98 | 0.90 | -0.03 | -0.08 | negative |
| 0.70 | 0.80 | 0.65 | -0.10 | -0.15 | negative |
| 0.50 | 0.50 | 0.50 | 0.00 | 0.00 | zero-EV boundary, not an entry |

No synthetic case is included in the Phase 6 historical accuracy or coverage results.
