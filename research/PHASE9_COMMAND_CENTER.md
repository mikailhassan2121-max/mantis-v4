# Phase 9 Command Center

> **PROXY SETTLEMENT REFERENCE**  
> **NO HISTORICAL WEBULL CONTRACT QUOTES**  
> **CLASSIFICATION RESEARCH ONLY**  
> **NOT A PROFITABILITY BACKTEST**  
> **LIMITED RECENT HISTORICAL REGIME**  
> **WEBULL AUTH NOT CONFIGURED**  
> **ECONOMIC THRESHOLDS NOT HISTORICALLY VALIDATED**  
> **OBSERVATION ONLY — NO AUTOMATED EXECUTION**

## Identity

`MANTIS` — **M**arket **A**nalysis and **N**euro-**T**actical **I**ntraday **S**ignal **S**ystem.

The wordmark appears once, spaced, at the top left. The expansion sits beside it in a lighter weight, the advisory banner `OBSERVATION ONLY — NO AUTOMATED EXECUTION` directly beneath it, and the build string below that. Every panel carries an all-caps title in muted cyan and, where useful, a right-aligned subtitle naming the asset or the provenance of what it shows. Panel names are the system's vocabulary: `PRIMARY DECISION`, `ASSET SURVEILLANCE`, `CONTRACT ECONOMICS`, `PROVIDER STATUS`, `VALIDATION`, `EVENT LOG`, `ADVANCED DIAGNOSTICS`, `CURRENT ADVISORY`.

No logo assets exist in the repository, so no logo was redesigned. The startup wordmark is a five-row block treatment in `theme.WORDMARK`, used only on the initialization screen.

### Palette

| Role | Colour | Used for |
|---|---|---|
| ground | graphite `#12161c` | screen background |
| panel | raised graphite `#181e26` | panel fill |
| structure | steel `#6f7f92` / `#4a5666` | borders, grid lines, labels |
| data | cool white `#dfe6ee` / `#a8b4c2` | primary and secondary values |
| identity | muted cyan `#4fb8c9` | titles, system identity, meters |
| ENTER YES | teal `#2fb6ad` | positive action state |
| ENTER NO | azure `#5f8fd6` | equally prominent, clearly distinct |
| WAIT | restrained amber `#d9a441` | neutral informational |
| NO TRADE | slate `#8593a6` | strong but non-alarming rejection |
| DATA HOLD | restrained red `#c85a54` | technical warning |

## Screen

Rendered from the real renderer at 200×50 with representative state. ADA is deliberately in `DATA HOLD`; ETH is deliberately classified `ENTER YES` but held by missing economics.

```text
┌─ MANTIS ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ M A N T I S   Market Analysis and Neuro-Tactical Intraday Signal System        UTC  2026-08-14 19:10:23           LOCAL  15:10:23 EDT        RUN  c5f8ebeb                   BUILD  MANTIS_V4_PHASE8 │
│ OBSERVATION ONLY — NO AUTOMATED EXECUTION                                      ● SYSTEM  OPERATIONAL              PROVIDER  LIVE             WEBULL  AUTH NOT CONFIGURED     LATENCY  0.34 s         │
│ MANTIS V4 / PHASE 9 COMMAND CENTER                                             MODEL  NORMAL_Z_PHASE6_LOCKED      MODE  OBSERVATION ONLY     COMMIT  546d8ca9f1              SCANS  1284             │
│                                                                                                                                                                                                      │
└──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────── H_p0.95_l0.90_f50_d.05_t300 ─┘
╔═ PRIMARY DECISION ══════════════════════════════════════════════════════════════════════════════════════════╗┌─ CONTRACT ECONOMICS ─────────────────────┐┌─ PROVIDER STATUS ─────────────────────────┐
║                              BTC-USD                                   ███     ███ █ █     ███ ███          ║│ EV STATUS             ROBUST POSITIVE EV ││ UNDERLYING                         ● LIVE │
║                   20260814T1500-1515-1786734000                         █      █ █ █ █  █    █   █          ║│ QUOTE                              VALID ││   SOURCE                            yahoo │
║                                                                         █  ███ █ █ ███     ███   █          ║│ QUOTE AGE                          2.4 s ││   LAST OK                        19:10:23 │
║                        [ ▲  ENTER YES  ▲ ]                              █      █ █   █  █    █   █          ║│ YES BID / ASK              0.928 / 0.941 ││   OK / FAIL                      1284 / 3 │
║                        ROBUST POSITIVE EV                               █      ███   █     ███   █          ║│ NO BID / ASK               0.052 / 0.069 ││   LATENCY                          0.34 s │
║                                                                     ████████████████████████░░░░░░░░░░      ║│ BREAK-EVEN                        94.10% ││ WEBULL              ○ AUTH NOT CONFIGURED │
║  P(YES) █████████████████████░                             97.1%      PAST T-300  WINDOW ELAPSED 69%        ║│ MODEL EDGE                         3.02% ││ ECONOMICS                      ○ DISABLED │
║  P(NO)  █░░░░░░░░░░░░░░░░░░░░░                              2.9%                                            ║│ LCB EDGE                           2.31% ││   SOURCE                 underlying-proxy │
║  LCB    ████████████████████░░                             92.7%                                            ║│ POINT EV                         +0.0302 ││ REFERENCE                PROXY UNVERIFIED │
║                                                                                                             ║│ LCB EV                           +0.0231 ││ QUOTE                         UNAVAILABLE │
║    CURRENT 118,431.55    REFERENCE 118,201.40    BUFFER 0.195%                                              ║│ RETURN / COST                       3.2% ││ SCANS                    1284  (0 errors) │
║                                                                                                             ║│ FEES                        UNKNOWN FEES ││ AUDIO / VOICE                   ON  /  ON │
║  SIGNAL  ENTER YES    ECONOMICS  ROBUST POSITIVE EV    SIDE  YES                                            ║│ SLIPPAGE                     NOT MODELED ││                                           │
╚══════════════════════════════════════════════════════════════════════════════════════════ PROXY_UNVERIFIED ═╝└──────────────────────────────── BTC-USD ─┘└───────────────────────────────────────────┘
┌─ ASSET SURVEILLANCE ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│┌─ BTC-USD  ◆ ───────────────────────┐┌─ ETH-USD ──────────────────────────┐   ┌─ SOL-USD ──────────────────────────┐   ┌─ XRP-USD ──────────────────────────┐   ┌─ ADA-USD ─────────────────────────┐│
││ PRICE                   118,431.55 ││ PRICE                     4,183.66 │   │ PRICE                     213.8840 │   │ PRICE                       2.9382 │   │ STATUS            NO CURRENT DATA ││
││ REFERENCE               118,201.40 ││ REFERENCE                 4,179.02 │   │ REFERENCE                 214.3100 │   │ REFERENCE                   2.9401 │   │ PRICE                           — ││
││ BUFFER           +230.1500  0.195% ││ BUFFER             +4.6400  0.111% │   │ BUFFER            -0.4260  -0.199% │   │ BUFFER            -0.0019  -0.065% │   │ REFERENCE                       — ││
││ P(YES)           ██████████  97.1% ││ P(YES)           ██████████  95.8% │   │ P(YES)            █░░░░░░░░░  7.7% │   │ P(YES)           ███░░░░░░░  31.3% │   │ P(YES)                          — ││
││ P(NO)                         2.9% ││ P(NO)                         4.2% │   │ P(NO)                        92.3% │   │ P(NO)                        68.7% │   │ SIDE                            — ││
││ SIDE               YES   LCB 92.7% ││ SIDE               YES   LCB 91.3% │   │ SIDE                NO   LCB 88.0% │   │ SIDE                NO   LCB 84.0% │   │ CLASS                   DATA HOLD ││
││ FRAGILITY           ███░░░░░  38.4 ││ FRAGILITY           ████░░░░  44.1 │   │ FRAGILITY           █████░░░  61.7 │   │ FRAGILITY           ██████░░  72.9 │   │ ECON        ECONOMICS UNAVAILABLE ││
││ CROSS RISK              6.2%  (1x) ││ CROSS RISK              9.4%  (2x) │   │ CROSS RISK             24.4%  (3x) │   │ CROSS RISK             33.1%  (4x) │   │ FINAL                 ■ DATA HOLD ││
││ CLASS                    ENTER YES ││ CLASS                    ENTER YES │   │ CLASS                         WAIT │   │ CLASS                         WAIT │   └────────────────────────── T--:-- ─┘│
││ ECON            ROBUST POSITIVE EV ││ ECON         ECONOMICS UNAVAILABLE │   │ ECON         ECONOMICS UNAVAILABLE │   │ ECON         ECONOMICS UNAVAILABLE │                                        │
││ FINAL                  ▲ ENTER YES ││ FINAL                       ◆ WAIT │   │ FINAL                       ◆ WAIT │   │ FINAL                       ◆ WAIT │                                        │
││ P(YES) TREND                ▁▂▄▅▇█ ││ P(YES) TREND                ▁▂▄▅▇█ │   │ P(YES) TREND                ▁▂▄▅▇█ │   │ P(YES) TREND                ▁▂▄▅▇█ │                                        │
└───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────── FIVE-ASSET CONTINUOUS SCAN ─┘
┌─ EVENT LOG ──────────────────────────────────────────────────────────────────────────────────────────────────────────┐┌─ VALIDATION ─────────────────────────────────────────────────────────────────┐
│ TIME          LVL            SOURCE          EVENT                             DETAIL                                ││ FORWARD OBSERVATION SAMPLE                             LIVE — NOT A BACKTEST │
│ 13:26:23      INFO           SYSTEM          MANTIS ONLINE                     run c5f8ebeb                          ││ SAMPLE STATUS                                                 EARLY EVIDENCE │
│ 15:00:00      INFO           SYSTEM          CONTRACT ROLLOVER                 20260814T1500-1515                    ││ CONTRACTS / ENTRIES                                                214 / 131 │
│ 15:01:51      INFO           SOL-USD         WAIT                              CONFIDENCE BELOW THRESHOLD            ││ RESOLVED / ABSTAIN                                               127 / 38.8% │
│ 15:03:41      NOTICE         ETH-USD         RESOLVED YES                      PROXY_RESOLUTION CORRECT              ││ CLASSIFICATION ACC                                    96.06%  [92.1%, 99.0%] │
│ 15:04:39      INFO           XRP-USD         WAIT                              FRAGILITY ABOVE THRESHOLD             ││ YES / NO ACCURACY                                 97.2% (n=71)  94.6% (n=56) │
│ 15:10:17      ACTION         BTC-USD         ENTER YES                         T-283s p=97.1%                        ││ PER ASSET                            ADA 95% BTC 97% ETH 96% SOL 96% XRP 96% │
│ 15:10:20      WARNING        ADA-USD         DATA HOLD                         STALE UNDERLYING DATA                 ││                                                                              │
│                                                                                                                      ││ HISTORICAL PROXY HOLDOUT                                  PHASE 6 — SEPARATE │
│                                                                                                                      ││ ACCURACY / COVERAGE                                          96.58% / 63.73% │
│                                                                                                                      ││ REGIME                                                 FORWARD REGIME NORMAL │
│                                                                                                                      ││ SAMPLES ARE NEVER COMBINED                                                   │
└────────────────────────────────────────────────────────────────────────────────── FORWARD LOGS ON DISK ARE COMPLETE ─┘└──────────────────────────────────────────────────────────────────────────────┘
```

## Regions

### Top header

Wordmark, expansion, advisory banner and build string on the left. On the right, four columns of three rows: UTC time, local time, run ID, build; system state, underlying provider state, Webull status, data latency; model version, operating mode, git commit, scan count. The locked policy name is the panel's right-hand subtitle. In demo mode an amber `DEMO / SYNTHETIC DATA — NOT A LIVE SIGNAL` band is added.

### Primary decision area

The visually dominant element. Top to bottom it answers: which asset and contract, what MANTIS favours, how confident it is, what the underlying is doing, whether the signal is eligible, and whether the contract is economically attractive.

The decision headline uses four independent carriers so it never depends on colour alone:

| Backend decision | Label | Glyph | Bracket | Panel border | Emphasis |
|---|---|---|---|---|---|
| `ENTER YES` | ENTER YES | ▲ | `[ … ]` | double | yes |
| `ENTER NO` | ENTER NO | ▼ | `< … >` | double | yes |
| `WAIT` | WAIT | ◆ | `: … :` | square | no |
| `NO TRADE THIS CONTRACT` | NO TRADE | ⊘ | `- … -` | square | no |
| `DATA HOLD` | DATA HOLD | ■ | `! … !` | heavy | yes |

The reason is always printed directly beneath the decision, translated from the backend reason code into operator language while the raw code stays available in the diagnostics panel:

```text
WAIT                          WAIT                              NO TRADE
CONFIDENCE BELOW THRESHOLD    CONTRACT ECONOMICS UNAVAILABLE    LCB EV <= 0

DATA HOLD                     ENTER YES
STALE UNDERLYING DATA         ROBUST POSITIVE EV
```

Below the headline: `P(YES)`, `P(NO)` and `LCB` as labelled bars with numeric values; the current price, reference and buffer; then a `SIGNAL / ECONOMICS / SIDE` strip that keeps the Phase 6 classification state visually separate from the Phase 7 economic state. On a wide screen the right half of the panel holds the block countdown.

### Contract countdown

`T-MM:SS` in a five-row block font, with a window-elapsed bar beneath it.

The value is `window_end - now`, where `window_end` is the resolution timestamp the backend published on the snapshot. The interface never computes a contract boundary; between five-second scans it interpolates against that same authoritative timestamp, which is what lets the countdown tick smoothly without inventing anything.

Emphasis is by colour temperature: cool white above T-120, amber from T-120, red from T-30. A `PAST T-300 / 180 / 120 / 60 / 30` marker names the most recent threshold crossed. These are display cues drawn from the policy's own timing vocabulary; they are not guarantees about what happens at those instants.

At rollover the contract ID changes, per-window values clear, and the new window visibly initialises from empty. An `INFO` line lands in the event log and one soft tone plays. There is no animation beyond the state change.

### Asset cards

One card per configured asset — BTC, ETH, SOL, XRP, ADA — showing symbol, price, reference, buffer (absolute and percent), `P(YES)` with a bar, `P(NO)`, preferred side with the lower bound, fragility with a meter, crossing risk with the crossing count, the Phase 6 classification state, the economic state, the final decision, the remaining time in the card subtitle, and a sparkline of the real `P(YES)` values observed this session.

The sparkline is drawn only from values the backend actually produced. No history is fabricated for decoration anywhere on the screen.

A card with no current data shows `NO CURRENT DATA` and em-dashes rather than the previous scan's numbers.

**Focus.** There is no validated cross-asset ranking, so none was invented. The configured `DEFAULT_FOCUSED_ASSET` holds the primary decision area unless one or more assets are in an ENTER state, in which case the longest-standing ENTER takes focus, tie-broken by configured asset order. No quality comparison between qualifying setups is performed anywhere.

On short or narrow terminals the cards collapse into one compact row per asset. All five assets always remain visible; the decision band gives up height first.

### Contract economics

EV status, quote status and quote age; YES and NO bid/ask; break-even probability; model edge; LCB edge; point EV; LCB EV; expected return on cost; fee and slippage status.

When economics are unavailable the panel says so and shows em-dashes rather than zeros — `EV ENGINE DISABLED`, `STATUS UNAVAILABLE`, `QUOTE NO VERIFIED BOOK` — while the classification continues to display normally. That is the required safe fallback:

```text
CLASSIFICATION      ENTER YES  96.4%
ECONOMICS           UNAVAILABLE
FINAL               WAIT — CONTRACT ECONOMICS UNAVAILABLE
```

### Provider status

Underlying provider state, source name, last success, cumulative successes/failures, latency; Webull status; economics engine state and source; reference status; quote status; scan and error counts; audio/voice state.

`AUTH_NOT_CONFIGURED` is drawn as a degraded capability in amber with a hollow-circle marker, not as an error. It is the expected state without credentials and it stops nothing.

### Validation

Two clearly separated blocks in one panel, with `SAMPLES ARE NEVER COMBINED` printed at the bottom:

- `FORWARD OBSERVATION SAMPLE`, subtitled `LIVE — NOT A BACKTEST`. Sample-status label, contracts observed, entry events, resolved entries, abstention rate, clustered classification accuracy with its 95% interval, YES and NO accuracy with counts, and per-asset accuracy. Sourced from `forward_report`, recomputed off the scan thread.
- `HISTORICAL PROXY HOLDOUT`, subtitled `PHASE 6 — SEPARATE`. The pinned 96.58% accuracy and 63.73% coverage, plus the regime-comparison status and any shift warnings from `compare_historical`.

The two are never averaged, blended, or shown under one heading.

### Event log

Timestamped feed in local time: time, severity, source, event, detail. Bounded in memory to `EVENT_LOG_LENGTH`; the panel subtitle reminds the operator that `FORWARD LOGS ON DISK ARE COMPLETE`. Repeated identical `WAIT` reasons for the same asset collapse to a single line so the feed stays readable.

### Auxiliary slot

Bottom right, in priority order: the error block when there is a recent failure, the advanced diagnostics panel when enabled, the validation panel otherwise, and the current-advisory table when validation is switched off. On terminals under 116 columns it holds provider status instead.

### Advanced diagnostics

Off by default; `--diagnostics` or `SHOW_ADVANCED_DIAGNOSTICS`. Window sigma, buffer Z, volatility regime, crossing probability and count, fragility, disagreement, data age, fetch latency, provider chain, quote validation status, quality reason, model version, policy name, config hash and git commit.

### Errors

Normal operation never shows a traceback:

```text
SYSTEM ERROR
Provider:  yahoo
Component: scan BTC-USD
Time:      2026-08-14T19:10:23+00:00
Recovery:  retrying / degraded mode
Detail:    HTTPError: 503 Service Unavailable
```

The full traceback is always appended to `data/logs/mantis_ui_diagnostics.log`. `DEVELOPER_MODE` additionally prints it in the panel.

### Terminal too small

Below 96×30 the screen is replaced by a panel naming the current size, the required size and the `--no-ui` fallback. The scanner keeps running throughout.
