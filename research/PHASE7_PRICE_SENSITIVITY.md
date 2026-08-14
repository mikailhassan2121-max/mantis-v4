# Phase 7 Price Sensitivity

> **PROXY SETTLEMENT REFERENCE**  
> **NO HISTORICAL WEBULL CONTRACT QUOTES**  
> **CLASSIFICATION RESEARCH ONLY**  
> **NOT A PROFITABILITY BACKTEST**  
> **LIMITED RECENT HISTORICAL REGIME**

For a $1 payout and before unverified fees, maximum executable purchase price is `max(0, p - required_edge)`. “pp” means probability percentage points, not percentage return.

| Model probability | Zero EV | +2 pp edge | +5 pp | +10 pp | +15 pp | +20 pp |
|---:|---:|---:|---:|---:|---:|---:|
| 0.80 | $0.80 | $0.78 | $0.75 | $0.70 | $0.65 | $0.60 |
| 0.85 | $0.85 | $0.83 | $0.80 | $0.75 | $0.70 | $0.65 |
| 0.90 | $0.90 | $0.88 | $0.85 | $0.80 | $0.75 | $0.70 |
| 0.95 | $0.95 | $0.93 | $0.90 | $0.85 | $0.80 | $0.75 |
| 0.97 | $0.97 | $0.95 | $0.92 | $0.87 | $0.82 | $0.77 |
| 0.99 | $0.99 | $0.97 | $0.94 | $0.89 | $0.84 | $0.79 |

Known acquisition costs reduce every maximum price dollar-for-dollar. None of the 2/5/10/15/20 pp gates has been optimized against historical Webull profitability because historical quotes do not exist.
