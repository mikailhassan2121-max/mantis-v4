# MANTIS — PHASE 3 DATASET NOTES

**Scope:** historical dataset construction, leakage-safe backtester, data-quality diagnostics,
cross-asset correlation analysis.
**Date:** 2026-08-13
**V3 status:** UNMODIFIED (MD5 `4fcb413eb59e07da1cc98645e2111a6e`).

> **Every reference in this dataset is an UNVERIFIED PROXY.**
> `reference_verified = 0`, `reference_source = PROXY_WINDOW_OPEN`, `economics_available = 0`
> on all 40,185 rows, enforced by `ProvenanceViolation` at write time.
> All results are **PRELIMINARY**. No model was trained. No threshold was tuned.

---

## 1. THE DATA CEILING, MEASURED

I probed the live API rather than assuming the limits:

```
single request : 8 days     "Only 8 days worth of 1m granularity data are
                             allowed to be fetched per request"
total reach    : ~28 days   "The requested range must be within the last 30 days"
beyond that    : NO DATA    (T-28d..T-35d and T-35d..T-42d both returned empty)
```

This is the binding constraint on all of Phase 3 (audit finding D-1). It is a property of the
bootstrap source, not a bug, and it is why every number in these reports carries a PRELIMINARY label.

### What was actually loaded

| Asset | Bars | Span | Completeness | Chunks | Gaps | Max gap | Longest stale run |
|---|---:|---:|---:|---:|---:|---:|---:|
| BTC-USD | 40,290 | 28.00 d | 99.93% | 4/4 | 28 | 3.0 min | 6 |
| ETH-USD | 40,290 | 28.00 d | 99.93% | 4/4 | 29 | 2.0 min | 5 |
| SOL-USD | 40,288 | 28.00 d | 99.92% | 4/4 | 30 | 3.0 min | 7 |
| ADA-USD | 40,289 | 28.00 d | 99.93% | 4/4 | 29 | 3.0 min | **18** |
| XRP-USD | 40,289 | 28.00 d | 99.93% | 4/4 | 29 | 3.0 min | 8 |

Zero duplicate timestamps, zero out-of-order bars, all indices tz-aware UTC. **No asset was
skipped** — all five cleared the 2,000-bar and 80%-completeness gates comfortably. Gaps are counted
and reported, never filled (requirement 9).

ADA's 18-bar identical-close run is worth flagging: 18 consecutive minutes at one price is the
signature that the Phase 2 feed-freeze detector (audit B-5) exists to catch. It is short enough to be
genuine low-liquidity behaviour rather than a stalled feed, but ADA is the asset to watch.

### Contracts

| Asset | Windows | Usable | No reference | No terminal bar | Base rate YES |
|---|---:|---:|---:|---:|---:|
| BTC-USD | 2,679 | 2,679 (100%) | 0 | 0 | 50.0% |
| ETH-USD | 2,679 | 2,679 (100%) | 0 | 0 | 50.3% |
| SOL-USD | 2,679 | 2,679 (100%) | 0 | 0 | 48.7% |
| ADA-USD | 2,679 | 2,679 (100%) | 0 | 0 | 45.5% |
| XRP-USD | 2,679 | 2,679 (100%) | 0 | 0 | 47.1% |
| **Total** | **13,395** | **13,395 (100%)** | **0** | **0** | **48.3%** |

100% usability is a consequence of 99.93% bar completeness, not of gap-filling — the two failure
paths (`NO_START_REFERENCE`, `NO_TERMINAL_BAR`) are implemented and unit-tested, they simply never
fired on this data.

**Dataset:** 40,185 rows = 13,395 contracts × 3 evaluable strategies. 38,443 with an entry.

---

## 2. HOW LOOK-AHEAD IS PREVENTED

### The barrier

A 1-minute bar indexed `t` covers `[t, t+60s)` and is not knowable until `t+60s`. So at scan `T`:

```
visible = { bar : bar.index + 60s <= T }
```

`HistoricalMarketView` computes that cutoff by binary search and exposes only `frame.iloc[:cutoff]`.
A strategy cannot reach a future bar because future bars are not in the object it is handed. There is
no discipline to violate.

### Documented divergence from live — and its direction

Backtest spot is the last **completed** bar's close, so on the 15-second scan grid it is 0–59 seconds
stale. Live, yfinance supplies the in-progress bar whose close tracks the current price.

**The backtest therefore has strictly less information than production.** Backtested performance is a
conservative estimate of live performance, not an optimistic one. The alternative — treating a
completed bar's close as the price at a mid-bar instant — would be genuine look-ahead, because that
close is the price at the end of a minute that has not finished.

### The one exception, and why it is justified

The proxy reference is the **Open** of the first bar at/after the window start. An opening price *is*
the instantaneous price at its own timestamp, so it is exposed from `bar.index` onward rather than
`bar.index + 60s`. If the opening bar is missing, the reference does not exist until the first bar
that is present, and `reference_available_at` enforces that — tested.

### Verified by mutation, not by assertion

A leakage test that passes trivially is worthless, so I broke the barrier on purpose:

```python
cutoff = scan_utc          # MUTATION: leak the in-progress bar
```

**Five independent tests failed**, including `test_view_excludes_incomplete_and_future_bars` and
`test_no_feature_snapshot_contains_future_information`. The mutation was then reverted and the suite
re-verified green. The tests have teeth.

### A performance mistake, and how it was fixed safely

My first implementation recomputed every indicator from full visible history at each of ~800,000
scans, with history growing to 40,000 bars. The run did not finish and I killed it.

The fix precomputes causal indicators once per asset and reads the row for each scan's last visible
bar. That is legitimate **only** because every formula used (`ewm`, `diff`, `pct_change`, `rolling`)
is causal, so computing over `frame[:t]` and reading the last row is identical to computing over the
full frame and reading row `t`.

That identity is load-bearing, so it is **proven, not assumed** — three tests:

- `test_precomputed_indicators_match_truncated` — recomputes from truncated history at seven offsets
  and requires an exact match to 9 decimal places on every column;
- `test_features_match_between_fast_and_slow_paths` — end-to-end feature-dict equality;
- `test_indicator_row_uses_no_future_bar` — multiplies all *future* bars by 5× and requires that no
  past indicator row changes.

If anyone later adds a non-causal feature, those tests fail immediately.

### Entry snapshot immutability

On entry the feature dict is `copy.deepcopy`'d, the scan loop breaks, and nothing rewrites it.
Settlement runs strictly afterwards. `test_entry_snapshot_is_a_copy_not_a_reference` mutates the
stored snapshot and confirms a fresh replay is unaffected.

---

## 3. CROSS-ASSET CORRELATION — THE MOST CONSEQUENTIAL FINDING

You asked for this because Phase 2 saw all five assets settle YES in one window. That single
observation proves nothing on its own. Measured across 2,679 simultaneous windows, it turns out to be
**systematic and large**.

| Measure | Value |
|---|---|
| Mean pairwise 1-minute return correlation | **0.641** |
| Mean pairwise outcome agreement | **73.8%** |
| Windows with all 5 assets present | 2,679 |
| Unanimous windows (all YES or all NO) | **1,241 (46.3%)** |
| Expected if independent | **6.3%** |
| **Effective independent assets per window** | **1.72 of 5** |

Distribution of YES-count across the five simultaneous contracts:

```
0 YES : 661  24.7%  ############
1 YES : 418  15.6%  #######
2 YES : 289  10.8%  #####
3 YES : 348  13.0%  ######
4 YES : 383  14.3%  #######
5 YES : 580  21.6%  ##########
```

The distribution is strongly **U-shaped**. Independence would produce a binomial hump centred on 2–3;
what actually happens is that the extremes (0 and 5) are the two most common outcomes, together
accounting for 46.3% of windows against 6.3% expected — a **7.3× excess**.

### Three consequences that bind on every later phase

**1. Every naive confidence interval in this report is too narrow.** Design effect = 5 / 1.72 = 2.91×
variance inflation, so intervals must widen by √2.91 = 1.71×. Both versions are given in the results
report. Effective sample size is ~4,400 contracts, not 13,395.

**2. Five simultaneous positions are one position, not five.** Sizing five coins as independent bets
understates tail risk by roughly 3×. This directly constrains Phase 13 (risk/sizing) and must not be
forgotten there.

**3. Walk-forward splits must group by window, not by row.** BTC and ETH rows for the same 15-minute
window on opposite sides of a fold boundary leak, even though each asset's series is chronologically
clean. `spans_from_replays` already assigns `group = contract_id` so same-window rows move together;
Phase 5 must actually use it.

---

## 4. PURGING AND EMBARGO

Built and tested now so Phase 5 inherits a correct tool rather than improvising one.

`purged_walk_forward` drops a training sample when its label window overlaps the test block (purge)
or begins within the embargo span after it. On the actual dataset, 5 folds with a 15-minute embargo:

```
fold 1: train= 2675  test= 2679  purged=4  embargoed=0
fold 2: train= 5355  test= 2679  purged=3  embargoed=0
fold 3: train= 8035  test= 2679  purged=2  embargoed=0
fold 4: train=10715  test= 2679  purged=1  embargoed=0
```

The purge counts are small (contracts barely overlap — each window's label depends only on its own
15 minutes) and the embargo count is legitimately **zero**, because with expanding chronological
folds nothing after the test block is ever in training. Both are asserted explicitly in tests rather
than glossed: `test_embargo_removes_samples_after_the_test_block` verifies the zero is correct rather
than a bug, and `test_overlapping_labels_are_purged` constructs deliberately overlapping 60-minute
spans and confirms purging fires.

`assert_no_overlap` re-verifies every fold and raises on leakage.

---

## 5. DATASET SCHEMA

One row per (contract, asset, strategy). Written to
`data/mantis_v4_historical_dataset.csv`, documented in `data/mantis_v4_dataset_schema.json`.

Core columns cover everything requirement 7 lists: `contract_id`, `asset`, `contract_start_utc`,
`contract_end_utc`, `reference`, `reference_verified`, `scan_count`, `seconds_remaining`, `spot`,
`buffer`, `buffer_pct`, `decision`, `entered`, `entry_side`, `entry_timestamp`, `terminal_price`,
`outcome_yes`, `outcome_label`, `prediction_correct`, `data_quality_status`, `regime` (placeholder),
`economics_available` — plus 50 `f_*` feature columns captured at the decision instant.

### Two deliberate V3 bug reproductions

A "V3 baseline" that quietly fixes V3's defects is not V3, and comparing V4 against a repaired V3
would flatter V4. Both bugs are reproduced and labelled:

| Column | Behaviour |
|---|---|
| `f_v3_rsi` | returns **50** on a zero-loss window (audit D-9; correct answer is 100) |
| `f_v3_realized_vol_1m` | log returns **span index gaps** (audit D-5) |
| `f_rsi`, `f_realized_vol_1m` | corrected V4 versions, computed alongside |

Empirically the volatility bug barely mattered on this data: median `v3_vol / corrected_vol` = 1.000,
because 99.93% completeness leaves almost no gaps to mis-handle. It would matter on a gappier feed.

### `prediction_correct` is NULL for abstentions

A NO TRADE is neither correct nor incorrect. Scoring abstentions as losses would corrupt every
accuracy statistic downstream, so the column is null and the metrics layer excludes them.

---

## 6. KNOWN LIMITATIONS

1. **The reference is a proxy, and this is the dominant caveat.** Everything here settles against the
   window's opening bar from Yahoo, not against a Webull contract specification. Audit UNKNOWN-3
   (settlement rule, reference source, timestamp, tie handling) is still open. If the venue settles
   against a TWAP or an index, every label in this dataset shifts.
2. **28 days is not multi-regime.** Master prompt §15 asks for bull/bear/sideways/high-vol/low-vol/
   shock coverage plus an untouched holdout. Four weeks of one market cannot deliver that. Results
   may not generalise to a different regime, and there is no way to know from this data.
3. **Effective sample size is ~4,400, not 13,395** (§3 above).
4. **No economic evaluation exists.** No historical contract prices, so no EV, no P&L, no
   break-even, no Kelly. Baseline D is reported NOT EVALUABLE rather than approximated.
5. **Backtest spot is 0–59s staler than live.** Conservative in direction, but live and backtest are
   not identical, and Phase 5 models trained here will see slightly staler features than production.
6. **Yahoo volume is not exchange-wide crypto volume** (§26J). `f_volume_ratio` is computed but
   should not be trusted without validation.
7. **`regime` is an unpopulated placeholder.** Phase 7 owns regime classification; emitting a guess
   here would be a fabricated feature.
8. **A single 28-day snapshot is one draw.** Re-running in a different month is not a fresh test of
   the same hypothesis unless treated as such.

---

## 7. FILES

| Path | Purpose |
|---|---|
| `mantis_v4/backtest/view.py` | the leakage barrier |
| `mantis_v4/backtest/features.py` | causal features, V3-faithful + corrected |
| `mantis_v4/backtest/strategies.py` | four baselines, one NOT EVALUABLE |
| `mantis_v4/backtest/replay.py` | event-driven backtester |
| `mantis_v4/backtest/metrics.py` | classification metrics (no economic fields) |
| `mantis_v4/backtest/splits.py` | purged/embargoed walk-forward |
| `mantis_v4/backtest/dataset.py` | schema + provenance-enforcing writer |
| `mantis_v4/backtest/diagnostics.py` | data quality + cross-asset |
| `mantis_v4/backtest/history.py` | replaceable bar-loading interface |
| `mantis_v4_backtest.py` | CLI runner |
| `tests/test_backtest_leakage.py` | 38 leakage/reconstruction tests |
| `tests/test_backtest_metrics.py` | metrics, splits, schema, diagnostics |
| `data/mantis_v4_historical_dataset.csv` | 40,185 rows |
| `data/mantis_v4_dataset_schema.json` | machine-readable schema |
| `data/mantis_v4_phase3_summary.json` | full numeric summary |
