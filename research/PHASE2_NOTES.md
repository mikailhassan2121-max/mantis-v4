# MANTIS — PHASE 2 RECORD

**Scope:** ContractSpec, exact contract semantics, provider interfaces, data-quality gates,
timestamp/timezone handling, resolution/outcome logging, configuration architecture, dependency
specification, dead-config elimination.
**Date:** 2026-08-13
**V3 status:** UNMODIFIED. `git diff HEAD -- mantis_15m_resolution_v3.py` is empty;
MD5 `4fcb413eb59e07da1cc98645e2111a6e`.

**No probability model was built. No thresholds were tuned. No performance claim is made.**
Every decision this build produces is `NO_TRADE`.

---

## 1. FILES CREATED / CHANGED

### Created

| Path | Purpose |
|---|---|
| `mantis_15m_resolution_v4.py` | Runnable entry point. Modes: `--selfcheck`, `--dataset`, live loop |
| `mantis_v4/__init__.py` | Package surface, version, phase status |
| `mantis_v4/clock.py` | Single time source, DST-safe, monotonic pacing, `FrozenClock` for tests |
| `mantis_v4/contracts.py` | `ContractWindow`, `ContractSpec`, `ContractQuote`, `ReferenceSource`, `SettlementRule` |
| `mantis_v4/config.py` | Config dataclass, env/local-file loading, credential redaction |
| `mantis_v4/quality.py` | Section-18 data-quality gate (15 checks, fail-closed) |
| `mantis_v4/recording.py` | `ContractRecorder` — SQLite + CSV, contracts & predictions |
| `mantis_v4/resolution.py` | `ResolutionEngine` — the outcome-logging guarantee |
| `mantis_v4/engine.py` | Scan orchestration |
| `mantis_v4/providers/base.py` | Provider ABCs, health, retry/backoff |
| `mantis_v4/providers/market_data.py` | yfinance adapter with timeout/retry/cache/staleness |
| `mantis_v4/providers/webull.py` | Official Webull adapter — graceful, non-fabricating |
| `mantis_v4/providers/local_file.py` | Manual JSON/CSV contract feed |
| `mantis_v4/providers/proxy.py` | Underlying-only fallback (unverified reference) |
| `mantis_v4/providers/chain.py` | Ordered fallback chain |
| `tests/test_contracts.py` | Window boundaries, DST, settlement, quotes, spec gating |
| `tests/test_recording.py` | Database writes, resolution, restart recovery |
| `tests/test_quality.py` | Every gate, fail-closed behaviour, bar helpers |
| `tests/test_providers.py` | Fallback order, degradation, retry, redaction |
| `tests/test_config_and_engine.py` | Config loading, dead-config regression, engine integration |
| `requirements.txt` | Pinned dependency floor with justification per entry |
| `config/mantis_v4.local.json.example` | Credential/config template (real file is gitignored) |
| `config/contracts/README.md` | Manual contract-spec format and rules |
| `research/PHASE1_V3_AUDIT.md` | Phase 1 deliverable (unchanged) |

### Changed

| Path | Change |
|---|---|
| `.gitignore` | Added MANTIS block: credentials, `data/`, `*.db`, generated CSV/JSON, `models/` |

### Untouched

`mantis_15m_resolution_v3.py` — protected baseline.

---

## 2. HOW CONTRACT RESOLUTION IS NOW RECORDED

This is the Phase 2 headline, because Phase 1 finding **A-2/C-1** established that V3 recorded no
outcome for any trade, ever. Its rollover handler was:

```python
if active_position is not None:
    active_position = None          # no log, no ledger row, no alert
    last_exit_time = time.time()
```

The result was a ledger with 31 feature columns and zero label columns.

### The lifecycle

1. **First observation.** The moment a window is seen for an asset, `observe_contract()` writes a
   `contracts` row with `status = OPEN`, keyed `contract_id|asset`. The row exists *before* any
   decision, so a contract cannot expire without a record waiting to be resolved.

2. **Every scan.** The row's `last_spot`, `high_spot`, `low_spot` and `scan_count` update, and an
   immutable row is appended to `predictions` — **including `NO_TRADE`**. Audit C-2: without
   abstentions recorded, abstention rate and the section-26O coverage curves are uncomputable.

3. **Entry (Phase 7+).** `record_entry()` stamps `entered`, `entry_time_utc`, `entry_side`,
   `entry_spot`.

4. **Resolution.** `ResolutionEngine.resolve_due()` finds every `OPEN` contract whose window has
   closed plus a grace period (default 120 s, so the terminal bar has time to publish), determines
   the terminal price, applies the recorded `settlement_rule`, and writes
   `terminal_price`, `terminal_source`, `terminal_bar_utc`, `outcome_yes`, `outcome_label`,
   `prediction_correct`, `resolved_at_utc`, `status = RESOLVED`.

5. **Failure is also an outcome.** If the terminal price cannot be determined, if the reference was
   never established, or if the settlement rule is `UNKNOWN`, the contract becomes
   `UNRESOLVED_NO_DATA` with a stated reason. Retries are bounded (default 20) so nothing sits in
   `OPEN` forever. **Nothing is ever deleted** — master prompt section 20.

### Why it cannot be skipped

`resolve_due()` runs in **three** places, deliberately redundantly:

- at **startup** (`recover_on_startup`) — recovers contracts orphaned by a crash, Ctrl-C, or a
  machine that slept through a boundary;
- on **every scan**;
- in the entry point's `finally` block on shutdown.

### Terminal-price convention

The terminal price is the **Open of the first 1-minute bar at or after the resolution instant** —
the same rule used for the window's opening reference. One rule for both ends keeps the measurement
symmetric, which matters because the *difference* between them is the label being learned; an
asymmetric open-vs-close convention would inject a systematic half-bar bias into every label.

Fallback is the Close of the last bar at or before resolution, accepted only within a 180 s
tolerance. Beyond that, the contract is marked unresolvable rather than approximated. The rule
actually used is recorded per contract in `terminal_source`.

### Verified end to end on live data

Not a simulation. A real run recorded five contracts for the real 20:30→20:45 ET window, the process
exited, the boundary passed, and a fresh process recovered and resolved all five from live Yahoo
bars:

```
STARTUP RECOVERY
  contracts open at startup : 5
  already expired           : 5
  resolved during recovery  : 5
  still open                : 0

  BTC-USD  ref=  63369.6406  term=  63460.3711  -> YES   via OPEN_AT_OR_AFTER(lag=+0s)
  ETH-USD  ref=   1884.0500  term=   1887.1300  -> YES
  SOL-USD  ref=     76.0100  term=     76.0800  -> YES
  ADA-USD  ref=      0.1823  term=      0.1828  -> YES
  XRP-USD  ref=      1.0094  term=      1.0106  -> YES

  ALL LABELS CONSISTENT: True
  prediction_correct: None on all five  (entered=0 — see below)
```

`prediction_correct` is `None`, not `0`. A NO TRADE is neither correct nor incorrect; scoring
abstentions as losses would corrupt every accuracy statistic downstream. Correctness is only defined
when a side was actually taken.

All five settled YES because the whole crypto market rose together in that window — an incidental
but useful reminder of the cross-asset correlation that master prompt section 4G says must not be
ignored, and of why a per-coin model treating assets independently is misspecified.

---

## 3. HOW PROVIDER FALLBACK WORKS

Chain order is fixed by `priority`, independent of construction order (tested):

| Priority | Provider | Reference source | Unlocks EV? |
|---|---|---|---|
| 10 | `OfficialWebullOpenAPIProvider` | `OFFICIAL_PROVIDER` | Yes, when verified + fresh quote |
| 50 | `LocalFileContractProvider` | `MANUAL_LOCAL` | **No** |
| 900 | `UnderlyingProxyContractProvider` | `PROXY_WINDOW_OPEN` | **No, ever** |

`ContractProviderChain.get_contract_spec()` asks each in order and returns the first spec that is
*usable* — which means more than non-`None`: a spec without a reference price is skipped, because
returning a shell that looks like success would let a caller believe a contract could be settled
when it cannot. Any provider that raises is caught, its health is recorded, and the chain continues.

### The EV lock is structural, not a policy

`ContractSpec.economics_available()` requires **all** of: reference source is
`OFFICIAL_PROVIDER`, settlement rule is a verified rule, the quote is two-sided, the quote passes a
sanity check, and the quote is fresh. `ReferenceSource.PROXY_WINDOW_OPEN.is_verified` is `False`, so
the proxy path cannot unlock EV even if a perfect, fresh quote is supplied alongside it. This is
tested directly.

A **manually typed** reference is also not verified. Typing a strike and verifying one must remain
visibly different things; declaring an explicit `settlement_rule` in the local file is the user's
attestation that they read it off the real contract, and it is recorded as such.

### Webull adapter: wired, honest, incomplete on purpose

Phase 1 confirmed from official documentation that event contracts are a first-class OpenAPI asset
class with real market-data endpoints (*Event Snapshot* / *Event Depth*, 2026-01-31; *Event Bars* /
*Event Tick*, 2026-03-14; streaming, 2026-03-28), that authentication is mandatory with App
Key/Secret, that a separate OpenAPI market-data subscription is required, and that the old
`openapi-python-sdk` repo is archived in favour of `webull-openapi-python-sdk`.

What Phase 1 could **not** confirm is the response **field names** (audit UNKNOWN-4), along with
UNKNOWN-1/2/3/5/6. So `_fetch_contract_payload` raises `ProviderUnavailable` with an explicit
message rather than calling an invented `client.get_event_snapshot(...)`. Writing a
plausible-looking call with guessed field names is the fabrication the master prompt forbids, and
the failure mode is worse than an error: it could bind to the wrong fields and produce confident
garbage economics.

Everything around that seam is real and tested — credential resolution, SDK detection, health
states, graceful degradation. States and their displayed status:

```
no credentials       -> AUTH NOT CONFIGURED
credentials, no SDK  -> SDK NOT INSTALLED
market data disabled -> UNAVAILABLE
otherwise            -> CREDENTIALS OK / SCHEMA NOT VERIFIED
```

In every one of those, `get_contract_spec` returns `None`, the chain falls through, and MANTIS keeps
running. Verified live:

```
WEBULL STATUS:        AUTH NOT CONFIGURED
LIVE CONTRACT QUOTES: UNAVAILABLE
EV ENGINE:            DISABLED

REFERENCE:            PROXY / UNVERIFIED
CONTRACT ECONOMICS:   UNAVAILABLE
EV ENGINE:            DISABLED
```

### Credentials

Read from `WEBULL_APP_KEY`, `WEBULL_APP_SECRET`, `WEBULL_MARKET_DATA_ENABLED`, `WEBULL_REGION`, or
from `config/mantis_v4.local.json` (gitignored; environment wins). `WebullCredentials.__repr__` and
`MantisConfig.__repr__` redact them to `<SET>`/`<UNSET>` — asserted by test. Nothing is hard-coded
and nothing is committed.

---

## 4. AUDIT FINDINGS CLOSED IN PHASE 2

| Finding | Was | Now |
|---|---|---|
| **A-1** CRITICAL | `contract_id` collided during the DST fall-back hour | ID carries the UTC epoch; the fall-back hour yields 8 distinct IDs (tested) |
| **A-2** CRITICAL | Resolution wrote nothing | Three-way redundant resolution; terminal status guaranteed |
| **A-3** HIGH | Cooldown leaked across the boundary | Removed; no cross-contract state exists in V4 |
| **A-4** HIGH | Clock read 8+ times per iteration | Read **once** per scan into an immutable `Instant`; gate rejects a mismatched window |
| **A-6** LOW | Dead rollover branch | Single resolution path |
| **C-1** CRITICAL | No outcome labels anywhere | `outcome_yes` / `outcome_label` / `prediction_correct` recorded and verified live |
| **C-2** HIGH | NO TRADE never recorded | Every scan appends a prediction row including abstentions |
| **C-3** MEDIUM | Predictions not durably pre-committed | Prediction rows are immutable and written before resolution |
| **C-4** MEDIUM | No restart recovery | `recover_on_startup` — verified on real orphaned contracts |
| **C-5** LOW | Bare `except: pass` on the primary store | SQLite errors propagate; only the secondary CSV mirror is guarded |
| **D-2** HIGH | 5-minute-old price allowed | 90 s default, surfaced in the UI |
| **D-3** HIGH | No network timeout anywhere | Explicit timeout + bounded retry + exponential backoff |
| **D-4** MEDIUM | Partial bar blended in silently | `last_bar_is_partial` carried and warned |
| **D-5** MEDIUM | Log returns computed across gaps | `contiguous_log_returns` drops gap-spanning returns; gaps reported |
| **D-6** MEDIUM | Data-age check failed **open** | Every check fails **closed**, including an outer guard |
| **D-8** MEDIUM | Naive timestamps silently assumed | Naive timestamps rejected; bars normalised to UTC on ingest |
| **E-1** CRITICAL | Proxy reference presented as a strike | Typed `ReferenceSource`; proxy can never read as official |
| **E-2** CRITICAL | `VALUE?` column with no economics | EV structurally locked behind verified spec + fresh quote |
| **E-3** HIGH | Phantom stop/target displayed | Not carried into V4 |
| **E-4** MEDIUM | `REWARD_RISK_RATIO` on a binary payoff | Not carried into V4 |
| **F-1** HIGH | 21 inert config constants | Regression test asserts every field is consumed; deferred ones listed explicitly |
| **F-2/F-3** MEDIUM | Dead functions, decorative scores | Not carried into V4 |
| **F-4** LOW | Display function mutated state | Rendering is pure; the engine owns state |
| **F-5** LOW | CICADA/MANTIS naming drift | Consistent naming |
| **G-2** MEDIUM | Loop drift, no compensation | `LoopPacer` with monotonic drift compensation and overrun reporting |
| **G-4** MEDIUM | No UTF-8 guard | `reconfigure(encoding="utf-8", errors="replace")` at startup |
| **G-6** LOW | Silent fallback to local time while printing "ET" | `TimezoneUnavailable` raised; MANTIS refuses to start on a wrong clock |
| **B-5** CRITICAL (partial) | Frozen feed produced 96.8% confidence | Feed-freeze detection by *price*, plus degenerate-volatility rejection. Full closure needs the Phase 4 model |

**Deferred by design** (they concern the model, which Phase 2 must not build): B-1, B-2, B-3, B-4,
B-6, B-7, B-8, A-5, D-1, D-7, G-1, G-3, G-5.

---

## 5. VERIFICATION RUN

```
$ python -m compileall -q mantis_v4 mantis_15m_resolution_v4.py tests
=== COMPILE OK ===

$ python -m unittest discover -s tests -t .
Ran 144 tests in 0.579s
OK

$ python -m pip install -r requirements.txt
numpy 2.5.2  pandas 3.0.5  yfinance 1.5.2  rich  scipy 1.18.0  sklearn 1.9.0  tzdata 2026.3
```

Section-22 coverage present in Phase 2: contract windows at all ten required boundary times;
DST fall-back and spring-forward; stale market data; missing candles; NaN/inf features; quote
freshness; database writes; restart/state recovery; no-trade logic. Deferred to their owning phases:
pending-entry and active-position across a boundary (Phase 7), EV and YES/NO payoff arithmetic
(Phase 8), model disagreement and fragility (Phase 7), historical feature leakage and walk-forward
split leakage (Phase 3), calibration output (Phase 5).

### A test found a real bug

`test_tz_naive_index_fails` initially **errored** rather than failing: the quality gate recorded the
timezone failure and then kept going, and the next check raised
`TypeError: can't subtract offset-naive and offset-aware datetimes`. A gate that throws instead of
returning a verdict is the same class of defect as one that fails open. Fixed by making the timezone
and ordering checks gate everything below them, and by adding an outer guard that converts any
unexpected exception into a blocking `gate_internal_error` failure.

Two other initial failures were **my test expectations being wrong, not the code**: the gap report
correctly reports a 6-minute inter-bar delta when 5 bars are removed, and the dead-config test needed
to count references the way the Phase 1 grep did rather than exclude `config.py` (where a field's
legitimate consumer may be a property).

---

## 6. REMAINING LIMITATIONS — READ BEFORE PHASE 3

1. **The reference is still a proxy.** Audit E-1 is *contained*, not solved. Every contract recorded
   so far carries `reference_verified = 0` and `settlement_rule = PROXY_TERMINAL_ABOVE_REFERENCE`.
   These are research labels derived from Yahoo 1-minute bars, **not** venue settlements. Phase 3
   must partition on `reference_verified` and must not present proxy-settled accuracy as though it
   were contract accuracy. **UNKNOWN-3 is unresolved.**

2. **No contract economics.** EV, edge, break-even and Kelly remain impossible. The UI shows `N/A`,
   not placeholders. Nothing may be called "positive EV" until a verified spec and a fresh quote
   exist together.

3. **The Webull adapter is incomplete on purpose.** UNKNOWN-1..6 stand. Completing it requires
   credentials and a reading of the live docs; the seam, the TODO list, and the health states are in
   place. In particular it is still **unconfirmed that 15-minute crypto event contracts exist at
   all** on the venue — the premise of the whole system.

4. **The dataset is tiny and starts today.** Five contracts exist. Live recording accumulates ~96
   windows/day/asset (~480/day across five assets), but the historical bootstrap is still bounded by
   yfinance's ~30-day 1-minute retention (audit D-1). Any Phase 3 result will be **PRELIMINARY**,
   reported with sample sizes and wide confidence intervals, per your instruction.

5. **No probability model exists.** Every decision is `NO_TRADE`. Nothing about accuracy or
   calibration can be said yet, in either direction.

6. **Partial-bar semantics are flagged but not yet neutralised.** `last_bar_is_partial` is recorded
   so Phase 3 can reproduce live conditions, but the backtester must actively simulate partial-bar
   formation or live and historical behaviour will diverge (audit D-4).

7. **Yahoo volume remains untrusted.** Per section 26J it is not exchange-wide crypto volume; no
   volume feature is used, and none should be added without validation.

8. **Alert audio and the full Rich UI are not in this build** (audit G-1/G-3, master prompt section
   19/26L/26M). Phase 2's renderer is a plain, honest table. Phase 9 owns the real UI and must keep
   `winsound.Beep` off the market loop's thread.

---

## 7. STATE OF THE PHASE PLAN

```
Phase 1  COMPLETE   audit                              research/PHASE1_V3_AUDIT.md
Phase 2  COMPLETE   contract semantics, providers,     this document
                    quality gates, recording, config
Phase 3  NEXT       historical dataset + leakage-safe backtester
Phase 4-10 PENDING
```

Awaiting approval before Phase 3.
