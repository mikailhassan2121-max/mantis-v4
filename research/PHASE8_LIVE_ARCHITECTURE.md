# Phase 8 Live Architecture

> **PROXY SETTLEMENT REFERENCE**  
> **NO HISTORICAL WEBULL CONTRACT QUOTES**  
> **CLASSIFICATION RESEARCH ONLY**  
> **NOT A PROFITABILITY BACKTEST**  
> **LIMITED RECENT HISTORICAL REGIME**  
> **WEBULL AUTH NOT CONFIGURED**  
> **ECONOMIC THRESHOLDS NOT HISTORICALLY VALIDATED**

`mantis_v4_live.py` is an observation-only five-asset runner. It captures one UTC instant per scan, identifies the exact quarter-hour window, obtains causal underlying bars, establishes an explicitly proxy reference when no verified contract reference exists, calculates Normal-Z and diagnostics, applies locked policy `H_p0.95_l0.90_f50_d.05_t300`, applies unchanged Phase 7 economics, renders a plain console snapshot, and appends forward records.

Quantitative logic, provider logic, append-only state, event hooks, reporting, and console presentation are separate modules. There is no order API and no code path that claims a user traded.

Fragility uses the Phase 6 development-evaluation median component scales frozen at deployment (`gamma=7.5376335387`, `vega=0.0130470891`, `theta=0.0045300536`, volatility-of-volatility `=0.3016632828`). It does not rescale itself from a single live row or future observations.

Window identity comes from `ContractWindow`: `:00→:15`, `:15→:30`, `:30→:45`, `:45→:00`. UTC arithmetic is authoritative and local Eastern time is display metadata. Crossing counters are keyed by asset/contract and rebuilt from prior observations after restart.

Events exposed for Phase 9 are `ON_ENTRY_YES`, `ON_ENTRY_NO`, `ON_WAIT`, `ON_DATA_HOLD`, `ON_CONTRACT_ROLLOVER`, `ON_RESOLUTION`, and `ON_ERROR`. No sound, voice, or final UI is implemented.

## Windows beginner startup

1. Open the project folder in File Explorer.
2. Click the address bar, type `powershell`, and press Enter.
3. Install dependencies once: `python -m pip install -r requirements.txt`.
4. Test one observation cycle: `python mantis_v4_live.py --once`.
5. Start continuous observation: `python mantis_v4_live.py`.
6. Stop safely with `Ctrl+C`. Existing JSONL records remain intact.

With no Webull credentials the runner prints `WEBULL_STATUS = AUTH_NOT_CONFIGURED` and continues classification-only. To provide verified manual economics, copy `config/contracts/example_economics.json` to `config/contracts/live_economics.json`, replace every example field with the active verified contract, and update its quote timestamp on every quote update. This live file is gitignored.
