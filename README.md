# mantis-v4

**MANTIS** — Market Analysis and Neuro-Tactical Intraday Signal System.

Research-grade 15-minute crypto event-contract probability, risk, and signal engine for BTC, ETH, SOL, ADA, and XRP.

> **OBSERVATION ONLY — NO AUTOMATED EXECUTION.** MANTIS never places an order, never holds broker credentials for execution, and never records that you traded. It observes, classifies, and advises.

## Quick start (Windows)

```powershell
python -m pip install -r requirements.txt
python mantis_v4_live.py
```

Maximise the window first — the command center wants at least 96×30 and looks best full-screen on 1920×1080. `Ctrl+C` stops it; forward records are append-only and survive any stop.

Complete beginner walkthrough, every flag, every setting, and what `AUTH NOT CONFIGURED` means: **[`research/PHASE9_OPERATOR_GUIDE.md`](research/PHASE9_OPERATOR_GUIDE.md)**.

```powershell
python mantis_v4_live.py --demo          # the interface on labelled synthetic data
python mantis_v4_live.py --test-alerts    # preview every tone and announcement
python mantis_v4_live.py --once           # one scan, one frame, exit
python mantis_v4_live.py --no-ui          # plain console output
```

## Phase documentation

| Phase | Subject | Documents |
|---|---|---|
| 1 | V3 audit | `PHASE1_V3_AUDIT.md` |
| 2 | Architecture rebuild | `PHASE2_NOTES.md` |
| 3 | Leakage-safe dataset and baselines | `PHASE3_DATASET_NOTES.md`, `PHASE3_BASELINE_RESULTS.md` |
| 4 | Models, calibration, ablation | `PHASE4_MODEL_RESULTS.md`, `PHASE4_CALIBRATION.md`, `PHASE4_FEATURE_ABLATION.md` |
| 5 | Simulation, fragility, diagnostics | `PHASE5_RESULTS.md` |
| 6 | Entry policy, timing, abstention | `PHASE6_ENTRY_POLICY.md`, `PHASE6_ENTRY_TIMING.md`, `PHASE6_ABSTENTION.md`, `PHASE6_SELECTIVITY_FRONTIER.md` |
| 7 | Contract economics | `PHASE7_CONTRACT_ECONOMICS.md`, `PHASE7_DECISION_INTEGRATION.md`, `PHASE7_PRICE_SENSITIVITY.md`, `PHASE7_WEBULL_PROVIDER.md` |
| 8 | Live runner and forward validation | `PHASE8_LIVE_ARCHITECTURE.md`, `PHASE8_FORWARD_VALIDATION.md`, `PHASE8_DATA_SCHEMA.md`, `PHASE8_FAILURE_RECOVERY.md`, `PHASE8_LIVE_VS_HISTORICAL.md` |
| 9 | Command center | `PHASE9_UI_ARCHITECTURE.md`, `PHASE9_COMMAND_CENTER.md`, `PHASE9_AUDIO_VOICE.md`, `PHASE9_OPERATOR_GUIDE.md` |

## Layering

```text
QUANT         unchanged Phase 5/6 mathematics      mantis_v4/simulation, mantis_v4/entry
PROVIDERS     market data + economics chain        mantis_v4/providers, mantis_v4/economics
STATE         append-only forward records          mantis_v4/forward
HOOKS         ON_ENTRY_YES ... ON_ERROR            mantis_v4/forward/events.py
PRESENTATION  command center, event log            mantis_v4/ui
AUDIO/VOICE   isolated, non-blocking               mantis_v4/ui/audio.py, voice.py
```

Nothing in `mantis_v4/ui` computes a probability, an expected value, an edge, a threshold or a contract boundary; the test suite enforces that.

## Tests

```powershell
python -m unittest discover -s tests -t .
```

## Status and limitations

The Phase 6 result — 96.58% classification accuracy at 63.73% coverage — is a **historical proxy-holdout** result, not a profitability backtest. It uses a proxy settlement reference and no historical contract quotes. Phase 7 economic thresholds are architectural, not historically validated. Without Webull credentials the EV engine stays disabled and eligible signals resolve to `WAIT — CONTRACT ECONOMICS UNAVAILABLE`, which is the system being honest rather than stuck.
