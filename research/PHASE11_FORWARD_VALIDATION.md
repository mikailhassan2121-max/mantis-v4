# Phase 11 — Live Forward Validation

MANTIS gathers uncontaminated forward classification evidence under frozen policy
`H_p0.95_l0.90_f50_d.05_t300`. Phase 11 does not retune thresholds or establish profitability.
Historical Phase 6 results remain separately labelled historical proxy classification results.

Live operation writes immutable observations, at most one entry per asset/window, separate
resolutions, provider health, run starts, clean-stop events, and no-signal/window-quality events.
Every outcome remains separate from entry-time inputs. Demo, self-test and soak modes cannot use
the real forward store.

Reports account for ENTER YES, ENTER NO, NO TRADE, DATA HOLD, PROVIDER FAILURE and INSUFFICIENT
DATA. Webull economics are optional and separate from classification validation.

Limitations: PROXY SETTLEMENT REFERENCE; CLASSIFICATION RESEARCH ONLY; NOT A PROFITABILITY
BACKTEST; forward accuracy does not establish profitability.
