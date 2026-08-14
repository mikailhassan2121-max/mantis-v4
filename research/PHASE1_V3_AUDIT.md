# MANTIS — PHASE 1 ENGINEERING AUDIT OF V3

**Audit target:** `mantis_15m_resolution_v3.py` (2,436 lines, unmodified)
**Audit date:** 2026-08-13
**Auditor scope:** Master prompt §1 (audit checklist) + §26A (Webull API investigation)
**Status of V3:** PROTECTED BASELINE — not modified, not overwritten, no changes proposed to this file.

---

## 0. VERIFICATION METHOD AND HONESTY STATEMENT

Every finding below is tagged:

- **[CONFIRMED]** — reproduced by executing code in this session. The reproduction is shown.
- **[ASSESSED]** — derived by reading the source. Logic is traced to specific line numbers but not executed.
- **[UNKNOWN]** — I could not determine this without credentials, live data, or vendor confirmation. It is recorded as an open question, not filled in with a guess.

I did **not** run the live V3 loop (it requires network + market hours + Windows audio). Findings about live behavior are marked `[ASSESSED]` unless the underlying arithmetic was executed in isolation.

**Provenance check performed first:** the V3 source embedded in
`MANTIS_V3_FULL_CODE_PLUS_V4_CLAUDE_MASTER_PROMPT.txt` (lines 5–2441) was diffed against the
standalone `mantis_15m_resolution_v3.py`. They are **byte-identical apart from one trailing blank
line**. The audit therefore describes a single, unambiguous baseline.

**No performance claim of any kind is made in this document.** V3's win rate is not stated, because
V3 has never recorded a single trade outcome (see Finding C-1). There is currently no data from which
any accuracy, calibration, or profitability number could be computed for either V3 or V4.

---

## 1. CURRENT ARCHITECTURE

### 1.1 Data flow

```
yfinance (1-minute bars, 4 tickers)
   │  yf.download(period=7d|1d, interval=1m)      ← primary
   │  yf.Ticker().history(period=7d)              ← fallback on failure/short data
   ▼
normalize_yfinance_data()   flatten MultiIndex, coerce numeric, drop NaN OHLC,
                            de-duplicate index (keep=last), sort
   ▼
merge_with_cache()          concat with in-memory cache, de-dup, cap at 5000 rows
   ▼
data_age_minutes()          reject if last bar > MAX_DATA_AGE_MINUTES (5)
   ▼
add_indicators()            EMA5/9/21, MACD(12,26,9)+hist+hist_change, RSI(14, EWM),
                            ATR(14, EWM), mom1/3/5/10, close_location, body_pct, volume_ratio
   ▼
analyze_market()            anchor → buffer → z → Φ(z) → "confidence" → worth_it
   ▼
find_best_setup()           filter worth_it, rank by (confidence, z), take top 1
   ▼
main() 3-state machine      IDLE → PENDING (locked signal) → ACTIVE → IDLE
```

State is held in four module globals (`active_position`, `pending_entry`, `last_exit_time`,
`market_cache`) plus `data_status`. Loop cadence is a flat `time.sleep(POLL_SECONDS=10)`.

### 1.2 The three-state machine (`main()`, lines 2133–2398)

| State | Entered when | Behaviour |
|---|---|---|
| **IDLE** | no pending, no position | scans all tickers, draws dashboard, may promote to PENDING |
| **PENDING** | a signal fired | locks to one ticker, refuses to scan others, waits `ASSUMED_ENTRY_DELAY_SECONDS`=7 for an assumed manual fill, or cancels |
| **ACTIVE** | assumed fill completed | monitors only that ticker until `check_exit()` fires or the clock rolls over |

Gating before IDLE→PENDING: `minutes_left > NO_NEW_ENTRY_MINUTES` (0.75), a 45 s
`COOLDOWN_SECONDS` since last exit, and `find_best_setup()` returning a candidate.

### 1.3 The probability formula (`analyze_market()`, lines 695–885)

```
anchor      = Open of the first 1-minute bar at/after the contract's ET start
buffer_pct  = (price − anchor) / anchor
vol_1m      = 0.70 · stdev(last 20 log returns) + 0.30 · stdev(last 60 log returns), floored at 1e-5
sigma_rem   = vol_1m · sqrt(max(minutes_left, 0.20))
raw_z       = buffer_pct / max(sigma_rem, 1e-5)
trend_adj   = clip(trend_points / 10, −0.55, +0.55)
adjusted_z  = raw_z + trend_adj
P(YES)      = Φ(adjusted_z)          ← math.erf-based normal CDF
confidence  = max(P(YES), P(NO))
strength    = clip(confidence · 100, 50, 97)
```

`trend_points` is an unweighted-by-evidence tally: EMA stack ±2.0, MACD hist sign ±0.8, MACD hist
slope ±0.35, four momentum signs (+0.20/0.45/0.60/0.70), an RSI band bonus ±0.35, and a momentum
acceleration sign ±0.30. Range is roughly ±5.5, so `trend_adj` saturates at ±0.55 well before the
extremes.

**The functional form of the buffer term is correct.** For a driftless random walk,
P(S_T > anchor) = Φ((S_t − anchor)/σ_remaining), which is what `raw_z` computes. That part is sound.
Everything layered on top of it is not — see Findings B-1 through B-5.

### 1.4 Entry / exit gates

`worth_it` (line 851) requires all of: `anchor_ready`, `elapsed ≥ 2.5 min`, `minutes_left > 0.75`,
`confidence ≥ 0.80`, `directional_z ≥ 0.55`.

`check_exit()` (line 1420) reads **only** four things: contract ID mismatch, `minutes_left ≤ 0.12`,
and (opposite direction AND `confidence ≥ 0.82`). It is otherwise a pure hold-to-resolution policy —
which is architecturally correct and should be preserved in V4.

---

## 2. FINDINGS

Severity: **CRITICAL** blocks V4 work or produces materially wrong decisions ·
**HIGH** materially degrades correctness · **MEDIUM** real but bounded · **LOW** hygiene.

---

### CATEGORY A — CONTRACT SEMANTICS AND STATE LEAKAGE

#### A-1 · CRITICAL · `contract_id` is not unique — it collides during the DST fall-back hour **[CONFIRMED]**

`current_contract_window()` builds the ID from local wall-clock strftime only:
`f"{start:%Y%m%d-%H%M}_{end:%H%M}"`. During the November fall-back, 01:00–02:00 ET occurs twice
(once EDT, once EST). Executed reproduction:

```
fold=0 01:20 (EDT) -> 20261101-0115_0130
fold=1 01:20 (EST) -> 20261101-0115_0130     ← same ID, physically different contract
```

Two distinct contracts share one identifier on the same calendar date. Live rollover detection still
fires correctly at the transition (the ID does change going into the repeated hour), so this is not a
live-freeze bug — but it **silently corrupts any historical dataset keyed on `contract_id`**, which is
exactly what Phase 3's label construction and Phase 15's walk-forward folds will be keyed on.

*I initially predicted this would also zero out `seconds_left` for the whole repeated hour. Testing
disproved that: CPython subtracts two aware datetimes sharing one `tzinfo` object as wall-clock, so
`seconds_left` stayed correct at 600 s. The ID collision is the real defect.*

**V4 requirement:** contract identity must be derived from the UTC instant (or ET instant + explicit
UTC offset), never from local wall-clock formatting alone.

#### A-2 · CRITICAL · Contract resolution is never logged; there is no outcome label anywhere **[CONFIRMED]**

The rollover block at lines 2148–2182 does this and only this when a position is open:

```python
if active_position is not None:
    active_position = None
    last_exit_time = time.time()
```

Grep of that block confirms **zero** calls to `log_event`, `ledger_event`, `voice_*`, or `sound_*`.
The primary, normal, expected termination of every hold-to-resolution trade — the contract expiring —
writes nothing. The user is holding a real Webull contract and MANTIS erases it from the display in
silence.

Consequences:
1. `mantis_resolution_v3_ledger.db/.csv` contains `SIGNAL`, `ENTRY_CANCELLED`, and `ASSUMED_FILL`
   rows but essentially never a terminal `EXIT` for the normal path.
2. **No outcome (win/loss) is recorded for any trade, ever.** The database has features but no labels.
3. Therefore no Brier score, log loss, calibration curve, accuracy, or EV can be computed from V3's
   own history. Master prompt §17 (online performance monitor) has nothing to monitor.

**V4 requirement:** at every boundary, resolve the contract explicitly — record terminal price,
settlement comparison, realized outcome, and update the prediction row. This is the single highest-value
fix in the entire audit, because without labels nothing downstream (§5, §6, §14, §15, §16, §17) can exist.

#### A-3 · HIGH · `last_exit_time` cooldown leaks across the contract boundary **[ASSESSED]**

Line 2156 sets `last_exit_time = time.time()` inside the rollover handler. Line 2347 then blocks all
new entries for `COOLDOWN_SECONDS` (45 s). A brand-new contract is therefore suppressed for its first
45 seconds *because the previous contract ended* — a textbook violation of master prompt §0 ("no …
state … derived specifically for the old contract may leak into the new contract"). Combined with
`MIN_ENTRY_ELAPSED_MINUTES = 2.5`, the practical entry window is already narrow; this shaves it further
for reasons that have nothing to do with the new contract.

#### A-4 · HIGH · Contract-rollover race: the clock is read 8+ times per iteration **[CONFIRMED]**

`current_contract_window()` appears at 14 call sites and each one calls `datetime.now()` independently.
Within a single loop iteration the clock is read by `main()` (L2143), by `analyze_market()` once **per
ticker** (L712 × 4), by `check_exit()` (L1435), and twice by `display_dashboard()` (L1741–1742) — with
a multi-second blocking yfinance fetch sitting between them.

If a boundary falls inside that window, one ticker's anchor is computed against the **new** contract
while `current_minutes` and the displayed contract label still describe the **old** one. Master prompt
§18 explicitly requires "no contract rollover race".

**V4 requirement:** resolve the `ContractWindow` **once** per scan and thread the immutable object
through every function. No function below `main()` may call `now()`.

#### A-5 · MEDIUM · Cross-contract indicator carryover is undocumented and unbounded **[ASSESSED]**

EMA/MACD/RSI/ATR/momentum are computed over the full 5,000-row cache, so at second 1 of a new contract
`trend_points` is fully determined by prior contracts' price action. This is *not* look-ahead (it is
strictly past data) and is defensible, but it is currently implicit. Given that `trend_adj` can supply
up to 65% of the evidence needed to fire a signal (Finding B-2), the design decision deserves to be
explicit, configurable, and validated — not accidental.

#### A-6 · LOW · `check_exit()`'s rollover branch is near-unreachable **[ASSESSED]**

Because `main()` already nulls `active_position` at the top of the iteration, the `contract_id`
mismatch check at line 1438 can only fire in a sub-second race between the two clock reads. The
user-facing "CONTRACT RESOLVED" alert it would produce is therefore almost never shown — reinforcing
A-2. The path that actually executes is the silent one.

---

### CATEGORY B — PROBABILITY MODEL AND CALIBRATION

#### B-1 · CRITICAL · An uncalibrated score is displayed to the user as a percentage confidence **[ASSESSED]**

`confidence = Φ(adjusted_z)` is rendered in the dashboard as `CONF 84.3%` (L1788) and
`STRENGTH 91.2%` (L1792) with a filled progress bar. Nothing in V3 has ever compared these numbers to
realized outcomes — and per A-2, it *cannot*, because outcomes are not recorded. This is precisely the
failure mode master prompt §6 and §20 prohibit.

The `strength` clamp `max(50, min(97, ·))` compounds it: it manufactures a floor of 50% and a ceiling
of 97%, so the display never shows the model's actual tails. A genuinely 99.8%-implied state and a
97.1% state render identically.

**V4 requirement:** until an out-of-sample reliability curve exists, this must be labelled
`MODEL SCORE`, never `CONFIDENCE %`.

#### B-2 · CRITICAL · The trend term is an unvalidated drift assumption worth up to 65% of the entry evidence **[CONFIRMED]**

`adjusted_z = raw_z + trend_adj` adds a hand-tuned quantity **in z-space**. Adding 0.55 to a z-score is
mathematically identical to asserting a drift of 0.55 · σ_remaining over the remaining window — a very
large directional claim, produced by dividing a tally of indicator signs by the magic constant 10.

Executed reproduction:

```
|z| required for confidence >= 0.80          : 0.8416
raw_z required WITHOUT trend help            : 0.8416
raw_z required WITH maximum trend help       : 0.2916
-> trend supplies up to 65.4% of the evidence needed to fire a signal
```

So a coin with a real buffer only one-third of the nominally required size can trigger a live entry
alert on indicator signs alone. Master prompt §20 forbids assuming drift estimates are reliable; V3
does exactly that, and weights it heavily.

#### B-3 · HIGH · `MIN_BUFFER_Z = 0.55` is dead — it can never bind **[CONFIRMED]**

`directional_z` is always `|adjusted_z|` (direction is chosen by the sign of `adjusted_z`), and
`confidence = Φ(|adjusted_z|)`. So `confidence ≥ 0.80` already implies `|z| ≥ 0.8416`. Executed:

```
confidence at z = 0.55 : 0.7088   (below the 0.80 gate — unreachable)
confidence gate implies z >= 0.8416, so MIN_BUFFER_Z = 0.55 can NEVER bind
```

The system therefore has **one** effective entry gate, not two. The "minimum buffer" safety check that
the config appears to provide does not exist. Anyone tuning `MIN_BUFFER_Z` is adjusting a no-op.

#### B-4 · HIGH · Gaussian terminal distribution is assumed, unvalidated, in a fat-tailed market **[ASSESSED]**

`normal_cdf` (L825) is the sole terminal-distribution model. 15-minute crypto returns are leptokurtic
and jump-prone; a Gaussian systematically **understates** tail probability, which means it
systematically **overstates** confidence at exactly the high-|z| values that trigger trades. The
direction of the error is adverse: the model is most overconfident precisely when it is about to act.

#### B-5 · HIGH · A near-frozen price feed produces near-maximum confidence **[CONFIRMED]**

`realized_vol_1m` is floored at `1e-5` and `sigma_rem` is divided into `buffer_pct`. When the feed
delivers repeated identical closes — common for thin alt bars on Yahoo, and the normal appearance of a
stalled feed — volatility collapses toward the floor and z explodes. Executed reproduction (40 flat
bars, then one 0.05% tick, 7 minutes remaining):

```
realized_vol_1m (floored) = 1.020e-04   sigma_remaining = 2.697e-04
buffer = 0.00050  ->  raw_z = 1.9  ->  confidence = 96.81%
```

A 0.05% move on a stalled feed reads as 96.8% confidence. `MAX_DATA_AGE_MINUTES` does not catch this:
it checks the **timestamp** of the last bar, never whether the **price** is moving. A stale-but-
timestamped feed passes the data gate and then produces the system's strongest signals.

**V4 requirement:** an explicit price-staleness / degenerate-volatility detector, plus a volatility
floor expressed in economically meaningful units rather than an arbitrary 1e-5.

#### B-6 · MEDIUM · Selection across 4 correlated coins is an unadjusted maximum **[ASSESSED]**

`find_best_setup()` takes the argmax confidence over 4 tickers. Taking the maximum of several noisy,
positively-correlated estimates biases the selected estimate upward (winner's curse). No
multiple-comparison penalty, no shrinkage. The reported confidence of the *selected* trade is
systematically higher than its true probability even if the per-coin model were perfectly calibrated.

#### B-7 · MEDIUM · Remaining-time floor caps confidence near expiry **[ASSESSED]**

`sqrt(max(minutes_left, 0.20))` floors remaining time at 12 seconds. As the contract approaches
resolution with a real buffer, true confidence should approach 1; V3's is capped by the floor. This is
conservative (fails safe) but arbitrary, and it interacts badly with §26H's "under-5-minute" baseline,
which is precisely the regime where V4 needs this term to be right.

#### B-8 · LOW · Tie at `adjusted_z == 0` resolves to YES **[ASSESSED]**

`if yes_probability >= no_probability` (L831) breaks the exact tie toward YES. Immaterial in
floating-point practice; noted for completeness because a directional default in a binary system
should be deliberate.

---

### CATEGORY C — LOGGING, LABELS, AND RESEARCH READINESS

#### C-1 · CRITICAL · The system has no labels, therefore no research substrate exists **[CONFIRMED]**

Consolidating A-2 into its research consequence: `LEDGER_FIELDS` (L892–924) contains 31 columns of
features and **not one outcome column**. There is no terminal price, no settlement comparison, no
win/loss, no realized P&L, no resolved-at timestamp.

This means **Phase 3 cannot begin from V3's logs.** The historical dataset must be reconstructed from
scratch out of raw 1-minute bars — which collides directly with Finding D-1 (yfinance retains only
~7–30 days of 1-minute history). This is the binding constraint on the entire V4 plan and needs a
decision before Phase 3 starts (see §4, Open Question 1).

#### C-2 · HIGH · NO-TRADE decisions are never recorded **[ASSESSED]**

`ledger_event` is called only for `SIGNAL`, `ENTRY_CANCELLED`, `ASSUMED_FILL`, and `EXIT`. Every scan
that concluded "no qualified setup" — the overwhelming majority, and the decisions master prompt §17
explicitly requires storing — leaves no trace. Abstention rate, coverage curves (§26O), and
selective-prediction analysis are all uncomputable.

#### C-3 · MEDIUM · Predictions are not written before outcomes occur, in a durable form **[ASSESSED]**

Master prompt §25 requires "its live predictions are logged before outcomes occur". V3 logs a feature
snapshot at signal time, but with no contract reference, no strike, no quote, no probability-vs-outcome
linkage, and no resolution row to close the loop. The audit trail cannot be used to prove the model
was not tuned after the fact.

#### C-4 · MEDIUM · No state persistence across restart **[ASSESSED]**

All state is in module globals. Restarting MANTIS mid-position discards the position entirely with no
record — the user is left holding a contract the system has forgotten. Master prompt §21 requires
"persist enough state to recover safely after restart".

#### C-5 · LOW · `ensure_ledger()` opens, creates, commits and closes a SQLite connection on every event **[ASSESSED]**

Wasteful but harmless at 10-second cadence. Both the SQLite write and the CSV write are wrapped in
bare `except: pass` (L1219, L1240) — a logging failure is completely invisible, including a disk-full
or permission error that would silently void the entire audit trail.

---

### CATEGORY D — DATA QUALITY, LOOK-AHEAD, AND TIMING

#### D-1 · CRITICAL · yfinance 1-minute history is the binding constraint on all validation **[ASSESSED]**

`BOOTSTRAP_PERIOD = "7d"`, `DATA_INTERVAL = "1m"`. Yahoo enforces a hard retention limit on 1-minute
bars (single request ≈ 7–8 days; total reach ≈ 30 days via chunking). Master prompt §15 asks for
chronological folds across "bull / bear / sideways / high vol / low vol / weekend / weekday / news
shocks" and a reserved untouched holdout.

**These two facts are in direct conflict.** ~30 days of 1-minute bars across 4–5 coins yields on the
order of 2,800 fifteen-minute contracts per coin — before purging, embargo, and holdout reservation.
For a model with dozens of candidate features that is a small, regime-poor sample, and it cannot span
a bull *and* bear *and* shock regime. Any walk-forward result built on it will have wide confidence
intervals, and I will report them as such rather than present a point estimate.

This is not a bug in V3 — it is a limit of the chosen data source, and it must be resolved (or
explicitly accepted) before Phase 3, not after.

#### D-2 · HIGH · `MAX_DATA_AGE_MINUTES = 5` permits acting on data one-third of the contract old **[ASSESSED]**

In a 15-minute contract, a 5-minute-old price is 33% of the contract's life. Combined with
`POLL_SECONDS = 10` and an uninstrumented yfinance round-trip, the true decision latency is unmeasured.
The dashboard displays this stale price as `PRICE` with no age indication.

#### D-3 · HIGH · No network timeout on any yfinance call **[CONFIRMED]**

Grep for `timeout` in V3 returns exactly two hits: the PowerShell voice subprocess (L332) and the
SQLite connection (L1208). Neither `yf.download()` nor `yf.Ticker().history()` is given a timeout. A
hung HTTP request stalls the entire market loop indefinitely — through a contract boundary, with a live
position open, with no alert. Master prompt §26N requires connect/read timeouts, retry, and backoff on
every external provider.

#### D-4 · HIGH · Partial in-progress candle mixes units in the momentum features **[ASSESSED]**

`latest = d.iloc[-1]` is the **currently forming** minute bar. Its `Close` is the live price (correct
and desirable), but its `High`, `Low`, and `Volume` are partial. Consequences:

- `mom1 = pct_change(1)` is a **partial-minute** return, compared directly against `mom3/3`, `mom5/5`,
  `mom10/10` which are **full-minute average rates**, inside `momentum_acceleration` (L801–809). Early
  in each minute `mom1` mechanically understates, so `momentum_acceleration` has a sawtooth bias that
  cycles once per minute.
- `ATR` and `volume_ratio` ingest a partial bar's range and volume.

This is a live/backtest consistency landmine: a Phase 3 backtester reading completed bars will not
reproduce live behaviour unless it explicitly simulates partial-bar formation.

#### D-5 · MEDIUM · Log returns are computed across index gaps without gap handling **[ASSESSED]**

`np.log(closes / closes.shift(1))` assumes adjacent rows are one minute apart. Yahoo crypto 1-minute
series contain gaps. A gap-spanning return is treated as a 1-minute return, inflating `vol_20`/`vol_60`
and deflating z. No `.asfreq()`, no gap mask, no timestamp-difference check.

#### D-6 · MEDIUM · `data_age_minutes()` fails open **[CONFIRMED]**

The `except Exception: return 0.0` at L567–568 reports **zero minutes old** — maximum freshness — for
any unexpected error. A data-integrity function whose failure mode is "everything is perfect" is
backwards; it should return `inf`.

*I predicted the tz-naive branch would raise on `pd.Timestamp.now().tz_localize(None)`. Testing on
pandas 3.0.5 disproved that — both branches compute correctly (a 6-year-old naive index reported
3,480,243 minutes). The fail-open remains a real design defect; the specific exception I predicted does
not occur on this pandas version.*

#### D-7 · MEDIUM · No look-ahead bias found in the live path **[ASSESSED]**

I specifically looked for future-data use and found none: indicators are causal (`ewm`, `diff`,
`pct_change`, `shift(1)`), the anchor is `d.index >= contract_start` (never the bucket's last bar), and
no `shift(-n)` / `.iloc[i+1]` / centred window appears anywhere. **V3's live path is clean on
look-ahead.** The risk is entirely prospective — it enters when Phase 3 builds the backtester, where
D-4 (partial bars) and A-1 (contract ID) are the two concrete traps.

#### D-8 · MEDIUM · Anchor timezone conversion is fragile on a tz-naive index **[ASSESSED]**

Lines 719–728 handle a tz-naive dataframe index via `contract_start.tz_localize(None)`, which converts
the ET contract start to a naive **ET wall-clock** value. If yfinance ever returns a naive index that
is actually **UTC**, the comparison is silently wrong by 4–5 hours, and the anchor is drawn from a
completely different contract. Currently yfinance returns tz-aware crypto indices, so this is latent —
but it is a silent wrong-answer path, not a crash path.

#### D-9 · LOW · RSI returns 50 instead of 100 in a pure uptrend **[CONFIRMED]**

`rs = avg_gain / avg_loss.replace(0, np.nan)` then `.fillna(50)`. When `avg_loss == 0`, RSI should be
100; V3 reports neutral. Executed:

```
40 consecutive up candles -> RSI = 50.0   (correct answer is 100)
V3's +0.35 band bonus (54 <= RSI <= 72) fires: False
```

The strongest possible uptrend reports neutral momentum and forfeits the RSI bonus. Impact is small
(±0.35 of ~5.5 trend points) and only in the rare zero-loss window, but it is unambiguously wrong.

---

### CATEGORY E — SETTLEMENT AND ECONOMIC ASSUMPTIONS

#### E-1 · CRITICAL · The anchor is a proxy for the settlement reference, and nothing verifies it **[ASSESSED]**

V3 uses the Open of the first 1-minute bar of the ET quarter-hour as its directional reference. The
docstring at L700–705 is admirably honest that this is a proxy — but the entire probability output,
the `BUFFER` column, and every entry decision are computed against it as if it were the strike.

Unverified assumptions stacked here:
1. that the contract settles against the window's opening price at all (vs. a TWAP, a decorated index,
   an external reference rate, or a fixed strike ladder);
2. that Yahoo's 1-minute Open for `BTC-USD` equals whatever price source Webull settles against;
3. that the contract boundary is ET-aligned rather than UTC-aligned;
4. that settlement is strictly-greater-than rather than at-or-above.

Master prompt §20 and §26B both explicitly forbid assuming "current 15-minute candle open == official
strike". **Every economic quantity in V3 is conditional on an unverified reference.**

#### E-2 · CRITICAL · No contract economics exist, so "worth it" cannot mean "positive EV" **[ASSESSED]**

There is no strike, no YES/NO bid/ask, no spread, no fee model, no quote timestamp, and no EV
calculation anywhere in the 2,436 lines. The dashboard column is literally headed `VALUE?` and shows
`YES` when `worth_it` is true (L1766, L1790) — but `worth_it` is a pure signal-quality flag with zero
economic content.

A user reading `VALUE? = YES` will reasonably infer the trade is worth its price. It cannot mean that.
A contract that is 84% likely and priced at $0.93 is a bad trade, and V3 has no way to know or say so.
This is the exact confusion master prompt §11 and §26G are written to eliminate.

To V3's credit, the file header states plainly: *"Signals only. Does NOT connect to Webull or place
orders. Underlying crypto movement != exact Webull event-contract P&L."* The honesty is in the header;
the UI does not carry it forward.

#### E-3 · HIGH · Stop / target / ATR risk machinery is computed, displayed, and never used **[CONFIRMED]**

`create_position()` and `activate_filled_position()` compute `initial_stop` and `target` from ATR.
Grep of all references:

```
1308,1309  written in create_position()
1486,1487  displayed in the giant entry alert  ("INITIAL STOP", "INITIAL TARGET")
1925,1929  displayed in the active-trade table ("STOP", "TARGET")
2117-2121  rewritten in activate_filled_position()
```

They are **never read by any decision function.** `check_exit()` reads only contract ID, minutes left,
direction, and confidence — it never sees price, stop, or target. The user is shown a red `STOP` and a
green `TARGET` for a stop-loss that does not exist and a take-profit that will never trigger.

This is a leftover of an earlier scalper design. The hold-to-resolution policy is correct for event
contracts and should be preserved in V4; the phantom risk display must not be.

#### E-4 · MEDIUM · `REWARD_RISK_RATIO` is meaningless for a binary payoff **[ASSESSED]**

Reward/risk = 1.15 is a continuous-payoff concept. An event contract's reward/risk is fully determined
by its price: `(1 − ask)/ask`. Carrying a configured R:R into V4 would be a category error.

---

### CATEGORY F — DEAD CODE AND CONFIGURATION HYGIENE

#### F-1 · HIGH · 21 of ~45 tuning constants are defined and never used **[CONFIRMED]**

Reference counts across the whole file (`1` = the definition line only):

```
MIN_ENTRY_SCORE  MIN_ENTRY_STRENGTH  BREAKEVEN_TRIGGER  LOCK_LEVEL_1  LOCK_LEVEL_2  LOCK_LEVEL_3
WEAKNESS_EXIT_SCORE  REVERSAL_EXIT_SCORE  PROFITABLE_SCORE_DROP_EXIT  PROFITABLE_STRENGTH_DROP_EXIT
PENDING_SCORE_DROP  PENDING_STRENGTH_DROP  PENDING_MAX_ADVERSE_MOVE  EARLY_FAILURE_SECONDS
EARLY_FAILURE_MOVE  CONVICTION_DROP_EXIT_POINTS  STRENGTH_DROP_EXIT_POINTS  SCALP_HEALTH_WARNING
SCALP_HEALTH_EXIT  SCALP_DECAY_MIN_PEAK  SCALP_DECAY_GIVEBACK
```

Nearly half the config surface is inert. `MIN_ENTRY_STRENGTH = 80.0` in particular reads as a live
safety gate and is not wired to anything. This is actively dangerous: it invites tuning knobs that do
nothing, and it obscures which parameters actually govern behaviour (there are five:
`MIN_RESOLUTION_CONFIDENCE`, `MIN_ENTRY_ELAPSED_MINUTES`, `NO_NEW_ENTRY_MINUTES`, `FORCE_EXIT_MINUTES`,
`EMERGENCY_OPPOSITE_CONFIDENCE` — plus `COOLDOWN_SECONDS` and `MAX_DATA_AGE_MINUTES`).

#### F-2 · MEDIUM · Dead functions and unconsumed features **[CONFIRMED]**

- `momentum_is_weakening()` (L1395) — defined, never called.
- `update_peak()` (L1326) — defined, never called; therefore `position["peak_price"]` is set once at
  creation and **never updated**, making it permanently equal to the entry price.
- `minutes_until_next_quarter()` (L241) — defined, never called.
- `close_location`, `body_pct` — computed every scan by `add_indicators()`, consumed by nothing.
- `volume_ratio` — computed, consumed by nothing (and see §26J: Yahoo crypto volume is not
  exchange-wide and should not be trusted uncritically anyway).

#### F-3 · MEDIUM · `calculate_scalp_health` and `calculate_profit_floor` are live but decorative **[ASSESSED]**

Both are computed, written to the ledger, and shown in the UI as `RESOLUTION HEALTH x/100` and
`SCALP FLOOR`. Neither influences any decision. `SCALP_HEALTH_WARNING`/`SCALP_HEALTH_EXIT` (F-1) are
the thresholds they would have been compared against. A 0–100 "health" number displayed next to a live
position, that no logic acts on, is misleading in the same way as E-3.

#### F-4 · LOW · Display functions mutate state **[ASSESSED]**

`display_dashboard()` calls `update_trade_peak(position, signal)` at L1860 — a render function
mutating trade state. If rendering is ever skipped, retried, or moved, peak tracking silently changes.

#### F-5 · LOW · Naming drift: "CICADA" vs "MANTIS" **[ASSESSED]**

The file is MANTIS, but `CICADA_LOGO`, `"CICADA IS TRACKING THIS SIGNAL ONLY"`, `"CICADA EXIT SIGNAL"`,
`"CICADA WILL EITHER ACTIVATE…"`, the thread name `CICADA-Voice`, and the pip hint `"start CICADA
again"` are all leftovers from a prior product name.

---

### CATEGORY G — WINDOWS TERMINAL, AUDIO, AND LOOP RELIABILITY

#### G-1 · HIGH · Alert audio and animation block the market loop **[ASSESSED]**

`winsound.Beep()` is **synchronous**. Blocking durations, summed from the frequency/duration tables:

| call | blocking time |
|---|---|
| `sound_entry()` | 1.52 s |
| `sound_exit()` | 2.15 s |
| `sound_profit()` | 1.53 s |
| `sound_warning()` | 1.10 s |
| `flash_panel()` (entry, 8 × 0.22 s) | 1.76 s |
| `flash_panel()` (exit, 8 × 0.14 s) | 1.12 s |
| explicit `time.sleep(10)` after exit (L2333) | 10.00 s |
| explicit `time.sleep(5)` after signal (L2398) | 5.00 s |
| explicit `time.sleep(4)` after cancel (L2262) | 4.00 s |
| explicit `time.sleep(1.5)` on contract reset (L2182) | 1.50 s |

An exit sequence blocks for **~13 seconds** of a 15-minute contract — 1.4% of the contract, during
which no price is read and no boundary is detected. Master prompt §21 requires "keep voice alerts
non-blocking" and "never let audio failures crash the market loop." Voice is correctly threaded
(`speak_async`); the beeps and flashes are not.

#### G-2 · MEDIUM · Loop cadence drifts; no monotonic compensation **[ASSESSED]**

The loop is `…work…; time.sleep(POLL_SECONDS)`. Actual period = 10 s + (4 sequential yfinance
round-trips) + render time. There is no measurement of the work phase and no compensation. Master
prompt §26K explicitly requires the monotonic pattern
`sleep(max(0, REFRESH_SECONDS − (monotonic() − loop_start)))`. Combined with D-3 (no timeout), a slow
Yahoo response silently stretches the effective scan interval with no user-visible signal.

#### G-3 · MEDIUM · `console.clear()` on every iteration causes flicker **[ASSESSED]**

Full-screen clear + full redraw at 10 s cadence, and at ~4.5 Hz during `flash_panel`. Rich's `Live`
renderer exists to avoid exactly this. §26K notes `cls`-style clearing should be reserved for a plain
CMD fallback mode.

#### G-4 · MEDIUM · UTF-8 dependency with no encoding guard **[ASSESSED]**

The UI relies on `█ ░ → ✓` and heavy box-drawing, and `CICADA_LOGO` uses full-block glyphs. There is no
`sys.stdout.reconfigure(encoding="utf-8")` and no code-page check. On a legacy `cmd.exe` at cp437/cp1252
this degrades or raises. §21 requires "maintain UTF-8 compatibility".

#### G-5 · MEDIUM · Voice spawns a PowerShell process per utterance **[ASSESSED]**

`_speak_windows` runs `powershell.exe -Command "Add-Type …SpeechSynthesizer…"` per phrase, with
`repeat` up to 3 and `timeout=20` each. Worst case is three sequential process spawns (~60 s bound) on
a daemon thread — non-blocking to the loop, but heavyweight, and it means an exit announcement can
still be speaking well after the contract has resolved.

Escaping is `text.replace("'", "''")`. Inputs are internal constants and ticker names, so injection
risk is currently nil — but the pattern is fragile if any user- or feed-supplied string ever reaches it.

#### G-6 · LOW · Timezone fallback silently mislabels **[ASSESSED]**

If `tzdata` is unavailable, `EASTERN` falls back to the machine's local zone (L129–132) while the UI
keeps printing "ET". For whole-hour-offset zones the 15-minute buckets happen to align, so the bug is
invisible; for half-hour zones (IST +5:30, ACST +9:30) the contract windows are misaligned by 30
minutes and the label is a lie. It should warn loudly, not degrade quietly.

---

## 3. WEBULL EVENT-CONTRACT API INVESTIGATION (master prompt §26A)

Investigated the current official documentation and SDK. **No endpoint, field, fee, or capability below
is invented; anything I could not confirm is marked UNKNOWN.**

### 3.1 Confirmed from official sources

- An official Webull OpenAPI exists at `developer.webull.com/apis/docs/`, covering stocks, options,
  futures, crypto and event contracts over HTTP, MQTT streaming and gRPC.
- **Event contracts are a documented first-class asset class**, with documented discovery endpoints:
  *Get Event Contract Categories*, *Get Event Contract Series*, *Get Event Contract Instruments*, plus
  a *Trade Event Subscription* for monitoring settlements.
- **Event-contract market data endpoints exist and are recent.** From the official changelog:
  - **2026-01-31** — added *Event Snapshot* and *Event Depth* to the event market module.
  - **2026-03-14** — added *Event Bars* and *Event Tick*; *Get Event Contract Instrument* gained
    `event_symbol`, `symbols`, `expiration_date_after` parameters and `event_symbol` / `event_name`
    response fields.
  - **2026-03-28** — "Event contracts now support real-time market data streaming" (quote, snapshot and
    tick subscriptions).
- Documented event-contract categories include **Crypto**.
- Order constraints: **LIMIT + DAY only**; MARKET/GTC are rejected. Order fields include `symbol`,
  `instrument_type = EVENT`, `event_outcome` ∈ {`yes`, `no`}, price range **$0.01–$0.99**.
- **Authentication is mandatory. There is no anonymous access.** Clients initialize as
  `ApiClient("<your_app_key>", "<your_app_secret>", "us")`; App Key/Secret are generated on Webull's
  site; `app_secret` is used client-side to compute a request signature and is not sent as a header.
  `WEBULL_APP_KEY` / `WEBULL_APP_SECRET` are supported as environment variables.
- **A separate OpenAPI market-data subscription is required** — subscriptions in QT or the mobile app
  do **not** carry over. A `403` indicates the subscription is missing. *(The docs state this
  explicitly for US stocks and ETFs; see UNKNOWN-2.)*
- Event-contract trading additionally requires opening an Event trading account and signing agreements.
- Official SDKs: Python (3.8–3.13) and Java (JDK 8+).
- **SDK repository status:** `webull-inc/openapi-python-sdk` (modules `webull-python-sdk-core`,
  `-trade`, `-mdata`, `-quotes-core`, `-trade-events-core`, `-demos`) is **archived and deprecated**;
  the docs direct migration to `webull-inc/webull-openapi-python-sdk`.

### 3.2 Open questions — NOT to be guessed

| # | Question | Why it blocks work |
|---|---|---|
| **UNKNOWN-1** | Do Webull's crypto event contracts include a **15-minute** cadence, and on which underlyings? | The entire V3/V4 premise. If the real cadence is hourly or daily, the contract-window abstraction changes. |
| **UNKNOWN-2** | Does the market-data subscription requirement extend to **event contracts** and **crypto**, or is it stated only for US stocks/ETFs? | Determines whether EV mode is reachable at all, and at what cost. |
| **UNKNOWN-3** | What is the exact **settlement rule** — reference source, timestamp, TWAP vs point-in-time, tie handling? | Directly determines E-1. Every probability is conditional on this. |
| **UNKNOWN-4** | The precise **field names** in *Event Snapshot* / *Get Event Contract Instruments* (strike/threshold, YES/NO bid/ask, expiration, quote timestamp). | The docs pages I read reference these endpoints but do not enumerate their response schemas. |
| **UNKNOWN-5** | Current **fee schedule** for event contracts. | §26M forbids claiming fee-adjusted EV without it; the FAQ defers to a pricing page. |
| **UNKNOWN-6** | **Rate limits** and whether a sandbox/test environment supports event market data. | Needed to size the polling cadence and to test without live capital. |

### 3.3 Consequence for the V4 architecture

The rules in §26A are satisfiable and no reverse-engineering is required or contemplated. V4 should
implement the provider interface with adapters in the mandated preference order:

1. `OfficialWebullOpenAPIProvider` — App Key/Secret via environment, official SDK only
2. authorized local bridge / user-supplied quote feed
3. manual local JSON / CSV provider
4. `UnderlyingOnlyFallbackProvider`

Until UNKNOWN-1 through UNKNOWN-5 are resolved **with real credentials**, V4 must run in the honest
degraded mode the master prompt specifies:

```
WEBULL CONTRACT DATA: UNAVAILABLE
CONTRACT ECONOMICS:   UNAVAILABLE
PROBABILITY MODEL:    UNDERLYING-ONLY
EV FILTER:            DISABLED
```

and display `N/A` — never a placeholder number — in every strike, quote, edge, and EV column.

---

## 4. WHAT PHASE 2 MUST RESOLVE BEFORE CODE IS WRITTEN

Three decisions are yours, not mine. Each changes what V4 actually is.

**Open Question 1 — the data problem (blocks Phase 3).**
Finding D-1 and Finding C-1 combine into the central obstacle: V3 has no labels, and yfinance can only
supply ~30 days of 1-minute bars to rebuild them from. Master prompt §15 asks for multi-regime
walk-forward validation and an untouched holdout, which ~30 days cannot honestly support. The realistic
options are (a) obtain a longer 1-minute history from another legitimate source, (b) start recording
now and accept a waiting period before validation is meaningful, or (c) proceed on ~30 days and report
every metric with wide, explicitly-stated confidence intervals. I will not manufacture a longer history.

**Open Question 2 — the settlement reference (blocks Phase 2 and all of §3/§8).**
Per Finding E-1 and UNKNOWN-3, the strike is unverified. Resolving it needs either Webull credentials or
a manual reading of a live contract specification. Until then `ContractSpec.reference` must be typed as
*proxy* and every downstream number labelled accordingly.

**Open Question 3 — Webull credentials.**
Do you have (or intend to obtain) an App Key/Secret and an OpenAPI market-data subscription? If yes, V4
builds the real provider and EV mode becomes reachable. If no, V4 is a directional-probability research
system only, and §3/§26G/§26M remain permanently disabled — which is a legitimate outcome, but it should
be a deliberate choice rather than a discovery in Phase 8.

Also worth noting for planning: **`scipy` and `scikit-learn` are not installed** in this environment
(verified: `pandas 3.0.5`, `numpy 2.5.2`, `yfinance 1.5.2`, `rich` present; `scipy`, `sklearn` absent).
§26D specifies `scipy.stats.norm.cdf` and §21 permits scikit-learn for calibration. V4 will need them
installed, or must degrade gracefully when they are missing.

---

## 5. WHAT V3 GETS RIGHT — PRESERVE THIS IN V4

Not a rewrite target. These are correct and should survive:

1. **Hard 15-minute clock bucketing** (`(minute // 15) * 15`) — verified correct at all ten boundary
   cases from the §22 test list (10:59:59 → 12:00:00 all produced the right window and the right
   seconds-remaining).
2. **Hold-to-resolution as the primary policy.** `check_exit()` deliberately refuses to scalp and only
   breaks on a genuine thesis reversal. This is architecturally right for a binary terminal payoff and
   must not regress into the scalper it was built from.
3. **The buffer-vs-remaining-volatility z-score.** `Φ(buffer / (σ₁ₘ·√t))` is the correct closed form
   for a driftless walk and is a legitimate transparent baseline — it is the honest core of the model.
   §26D asks for exactly this; V3 already has it. The defects are in what is added to it (B-2) and what
   is assumed about its distribution (B-4).
4. **Anchor is taken from the contract's own first bar, and `anchor_ready` is false when that bar is
   missing** — the code explicitly refuses to reuse the prior contract's anchor at the boundary. The
   comment at L731–733 shows the leakage risk was understood.
5. **Signal lock / assumed-fill modelling.** Acknowledging that a human takes seconds to place the
   order, and re-validating during that gap, is a genuinely good design for manual decision support.
6. **Refusal to invent data.** `if signal is None: … "CICADA will not invent an exit from missing
   data"`. `DATA HOLD` on insufficient data. Cache-with-staleness rather than fabrication.
7. **Honest header disclaimer** — signals only, no orders, underlying ≠ contract P&L.
8. **Dual CSV + SQLite ledger with indices** — the right shape, missing only the outcome columns.

---

## 6. SUMMARY

| Severity | Count | Findings |
|---|---|---|
| CRITICAL | 8 | A-1, A-2, B-1, B-2, C-1, D-1, E-1, E-2 |
| HIGH | 12 | A-3, A-4, B-3, B-4, B-5, C-2, D-2, D-3, D-4, E-3, F-1, G-1 |
| MEDIUM | 16 | A-5, B-6, B-7, C-3, C-4, D-5, D-6, D-7, D-8, E-4, F-2, F-3, G-2, G-3, G-4, G-5 |
| LOW | 7 | A-6, B-8, C-5, D-9, F-4, F-5, G-6 |
| **Total** | **43** | of which **D-7 is a clean bill of health** (no look-ahead found in the live path) |

**The three findings that gate everything else:**

1. **C-1/A-2 — there are no outcome labels.** Nothing in Phases 3, 5, 6, 10, 14, 15, 16 or 17 can be
   built or validated until resolution outcomes are recorded. This is the first thing V4 must fix.
2. **E-1/E-2 — the settlement reference is a proxy and there are no contract economics.** Until
   resolved, V4 can research direction but must never say "positive EV" or "value".
3. **B-1/B-2 — an uncalibrated, drift-contaminated score is presented as a confidence percentage.**
   This must be renamed to `MODEL SCORE` on day one and may only become `CONFIDENCE` if and when an
   out-of-sample reliability curve earns it.

No rewrite has begun. `mantis_15m_resolution_v3.py` is unmodified. Awaiting approval to proceed to
Phase 2.

---

### Sources (Webull investigation)

- [Webull OpenAPI — product page](https://www.webull.com/open-api)
- [Webull API — documentation home](https://developer.webull.com/apis/docs/)
- [Webull API — Event Contract Trading](https://developer.webull.com/apis/docs/trade-api/event-contract/)
- [Webull API — Change Logs](https://developer.webull.com/apis/docs/changelog/)
- [Webull API — Market Data Getting Started](https://developer.webull.com.au/apis/docs/market-data-api/getting-started/)
- [Webull API — FAQ](https://developer.webull.hk/apis/docs/faq/)
- [Webull API — SDKs and Tools](https://developer.webull.com/apis/docs/sdk/)
- [github.com/webull-inc/openapi-python-sdk (archived)](https://github.com/webull-inc/openapi-python-sdk)
- [github.com/webull-inc/webull-openapi-python-sdk (current)](https://github.com/webull-inc/webull-openapi-python-sdk)
