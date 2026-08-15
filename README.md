# MANTIS 4.10.0

**MANTIS** — Market Analysis and Neuro-Tactical Intraday Signal System.

Research-grade 15-minute crypto event-contract probability, risk, and advisory engine for BTC, ETH, SOL, ADA, and XRP.

> **OBSERVATION ONLY — NO AUTOMATED EXECUTION.** MANTIS never places an order. It observes, classifies, records forward evidence, and presents advisory states.

## Quick start for Windows

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_mantis.ps1
.\start_mantis.bat --demo
```

Setup creates only a project-local `.venv`, installs declared dependencies, and runs a safe health check. It does not require Administrator access. After checking demo mode, run `.\start_mantis.bat` for continued real forward observation. `Ctrl+C` performs a clean subsystem shutdown.

Full beginner instructions: [`research/PHASE10_INSTALLATION.md`](research/PHASE10_INSTALLATION.md).

```powershell
python mantis_v4_live.py --demo           # labelled synthetic presentation only
python mantis_v4_live.py                  # real forward observation
python mantis_v4_live.py --no-browser     # server/scanner without opening Chromium
python mantis_v4_live.py --no-voice       # suppress speech
python mantis_v4_live.py --no-audio       # suppress tones
python mantis_v4_live.py --health-check   # installation/config diagnostics only
python mantis_v4_live.py --self-test      # isolated temp persistence + web checks
python mantis_v4_live.py --profile quiet  # UI on, sound and speech off
python mantis_v4_live.py --forward-report # read-only Phase 11 evidence summary
python mantis_v4_live.py --daily-report   # today's UTC forward summary
python mantis_v4_live.py --forward-manifest # counts, schemas, and file hashes
```

## Architecture

```text
QUANT         locked Phase 6 mathematics            mantis_v4/simulation, entry
ECONOMICS     Phase 7 quote/EV semantics             mantis_v4/economics
STATE         Phase 8 append-only forward records   mantis_v4/forward
PRESENTATION  Phase 9 local HTTP/SSE command center mantis_v4/ui
RUNTIME       Phase 10 health/lifecycle/recovery    mantis_v4/health.py, runtime.py
EVIDENCE      Phase 11 read-only forward analytics mantis_v4/forward/phase11.py
```

Nothing in `mantis_v4/ui` computes a probability, EV, edge, threshold, or contract boundary. Presentation, audio, voice, browser, and SSE failures are isolated from scanning.

## Phase documentation

| Phase | Subject | Primary documents |
|---|---|---|
| 1–5 | Audit, causal data, models, simulation | `research/PHASE1_*` through `PHASE5_*` |
| 6 | Locked entry classification | `research/PHASE6_ENTRY_POLICY.md` |
| 7 | Contract economics | `research/PHASE7_CONTRACT_ECONOMICS.md` |
| 8 | Forward validation | `research/PHASE8_LIVE_ARCHITECTURE.md`, `PHASE8_FAILURE_RECOVERY.md` |
| 9 | Command center | `research/PHASE9_UI_ARCHITECTURE.md`, `PHASE9_OPERATOR_GUIDE.md` |
| 10 | Production hardening | `research/PHASE10_HARDENING.md`, `PHASE10_FAILURE_RECOVERY.md`, `PHASE10_INSTALLATION.md`, `PHASE10_RELEASE_CHECKLIST.md` |
| 11 | Live forward observation | `research/PHASE11_FORWARD_VALIDATION.md`, `PHASE11_OPERATOR_GUIDE.md` |

## Verification

```powershell
python -m unittest discover -s tests -t .
python -m mantis_v4.soak --windows 250
python mantis_v4_live.py --health-check
python mantis_v4_live.py --self-test
```

At shutdown MANTIS joins the reporter, closes HTTP/SSE, stops audio and voice, and closes only the dedicated browser child it launched. Raw forward JSONL is never rotated or deleted automatically; only the separate technical diagnostic log has bounded rotation.

## Research limitations

- PROXY SETTLEMENT REFERENCE
- NO HISTORICAL WEBULL CONTRACT QUOTES
- CLASSIFICATION RESEARCH ONLY
- NOT A PROFITABILITY BACKTEST
- LIMITED RECENT HISTORICAL REGIME
- WEBULL AUTH NOT CONFIGURED unless explicitly configured and verified
- ECONOMIC THRESHOLDS NOT HISTORICALLY VALIDATED

The Phase 6 historical proxy-holdout result is not evidence of guaranteed accuracy or profitability. Missing verified contract economics correctly produces WAIT rather than an invented economic conclusion.
