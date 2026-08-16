# Step 9 — Kalshi Forward Reference-Risk Shadow

Status: `FORWARD_SHADOW_ONLY`
Reference policy: `KALSHI_REFERENCE_RISK_V1_SHADOW`
Sample label: `NEW_CAUSAL_FORWARD_SAMPLE`
Production authorization: `FALSE`

Step 9 collects genuinely forward, causal evidence without creating a trade
recommendation. It uses the dynamically verified Kalshi target for the exact
BTC/ETH/SOL/XRP quarter-hour contract, Yahoo as an explicitly labelled current
value proxy, and the frozen transparent MANTIS probability machinery. It does
not instantiate the production selector, UI alerts, voice, authenticated APIs,
or execution code.

## Isolated append-only data

The default directory is `data/kalshi_forward_shadow/`. It contains:

- `runs.jsonl`
- `observations.jsonl`
- `shadow_candidates.jsonl`
- `resolutions.jsonl`
- `provider_health.jsonl`
- `audit_events.jsonl`

Observations never contain outcomes. Final official Kalshi results are appended
to `resolutions.jsonl`. Deterministic observation, candidate, contract, and
resolution IDs prevent duplicate records after restart. A partial final JSONL
line is tolerated as crash residue; an interior malformed line fails closed.
The existing `data/forward/` namespace is neither opened nor modified.

## Frozen parallel shadow policies

The following were declared before forward outcomes are observed:

1. `BASE_TRANSFERRED`
2. `FIXED_2BP`
3. `FIXED_5BP`
4. `FIXED_10BP`
5. `DEV_P95`
6. `DEV_P99`

The development P95/P99 values are loaded from the frozen Step 8 summary and
its identity is checked. Missing bounds produce `REFERENCE_UNKNOWN`; unknown is
never treated as robust. The transferred numerical gates remain unchanged.

## Timing and settlement

At each configured MANTIS scan cadence, each of the four assets is attempted.
The current Kalshi mapping must match the exact window and be active. A market
that is still initializing produces a provider-health record and no causal
observation. A prior-window mapping or quote is never carried forward.

After close, the collector reads the official public market result. It records
`YES` only when the asset-specific rounded official `expiration_value` is
greater than or equal to `floor_strike`, and verifies this against Kalshi's
final result. Missing results remain pending; Yahoo is never used to infer a
resolution.

## Commands

Start forward shadow collection:

```powershell
python mantis_v4_live.py --kalshi-forward-shadow
```

Run one safe collection cycle:

```powershell
python mantis_v4_live.py --kalshi-forward-shadow --once
```

Audit the append-only dataset:

```powershell
python mantis_v4_live.py --kalshi-forward-audit
```

Report matured observations:

```powershell
python mantis_v4_live.py --kalshi-forward-report
```

The report keeps all six policies separate and includes asset/side breakdowns,
Brier score, log loss, calibration, distance/time/volatility groups, and a
window-cluster bootstrap interval. Milestones are 50, 100, 250, 500, 1,000,
and 2,000 resolved asset-contract observations; distinct windows are always
reported because four assets in one window are correlated.

## Promotion boundary

No policy promotes automatically. A later manual research review should have
at least 500 resolved asset-contract observations (preferably 1,000), adequate
distinct windows, all assets and sides represented, no integrity failures, and
no obvious calibration or regime breakdown. These criteria do not establish
profitability and do not authorize trading.
