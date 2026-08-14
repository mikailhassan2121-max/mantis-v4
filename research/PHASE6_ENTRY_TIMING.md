# Phase 6 Entry Timing

> **PROXY SETTLEMENT REFERENCE**
> **NO HISTORICAL WEBULL CONTRACT QUOTES**
> **CLASSIFICATION RESEARCH ONLY**
> **NOT A PROFITABILITY BACKTEST**
> **LIMITED RECENT HISTORICAL REGIME**

Historical future states below evaluate predefined decisions; they are never live features.

| Bucket | States | Accuracy now | Direction flips later | Confidence improves | Confidence deteriorates | Setup disappears |
|---|---:|---:|---:|---:|---:|---:|
| earlier than T-600 | 13400 | 63.53% | 58.84% | 99.11% | 52.04% | 0.91% |
| T-600 to T-450 | 10050 | 74.00% | 40.23% | 95.43% | 50.49% | 1.34% |
| T-450 to T-300 | 6700 | 78.46% | 29.90% | 88.66% | 41.55% | 2.13% |
| T-300 to T-180 | 6700 | 82.85% | 21.25% | 75.81% | 31.49% | 3.22% |
| T-180 to T-120 | 3350 | 85.49% | 14.03% | 62.45% | 21.13% | 4.90% |
| T-120 to T-60 | 6700 | 88.48% | 7.37% | 46.09% | 13.39% | 6.63% |
| T-60 to T-30 | 3350 | 91.13% | 0.00% | 38.12% | 0.00% | 8.36% |
| T-30 to resolution | 3350 | 91.13% | 0.00% | 0.00% | 0.00% | 0.00% |

Near-resolution accuracy is explicitly classification-trivial when dominated by saturated probability, large buffers, and collapsed remaining volatility. No economic usefulness can be inferred without quotes.
