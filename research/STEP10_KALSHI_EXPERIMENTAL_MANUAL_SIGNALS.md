# Step 10 — Experimental Kalshi manual signals

Status: `EXPERIMENTAL_MANUAL_SIGNAL_ONLY`

This mode is an operator research aid. It never authenticates to Kalshi, reads
an account, submits an order, sizes a position, or records that the operator
traded. The screen and optional voice announcement are informational; the
human operator makes every decision and performs any action separately.

Run:

```powershell
python mantis_v4_live.py --kalshi-manual-signals
```

One-scan backend diagnostic:

```powershell
python mantis_v4_live.py --kalshi-manual-diagnostics
```

The manual command publishes an authoritative `KALSHI_MANUAL_SIGNAL` state
before opening the browser. The first screen therefore says `SCANNING` and
contains BTC/ETH/SOL/XRP rows even while the first provider scan is in flight.
After that scan, the same backend state supplies the UI and SSE payload with
the current candidate statuses and `seconds_until_entry_eligible`; the browser
does not derive selection or economics.

The existing shadow collector remains separate and unchanged:

```powershell
python mantis_v4_live.py --kalshi-forward-shadow
```

## Fixed policy

- Universe: BTC-USD, ETH-USD, SOL-USD, XRP-USD.
- Probability model: `KALSHI_REFERENCE_V1` /
  `TERMINAL_PRICE_CONSERVATIVE_PROXY_NO_SQRT60`.
- Qualification gates are the transferred, unretuned gates.
- Reference gate: `KALSHI_REFERENCE_RISK_V1_SHADOW / DEV_P95` using the frozen
  Step 8 bounds: BTC 5.47 bp, ETH 7.03 bp, SOL 7.58 bp, XRP 6.52 bp.
- Event targets and order books come from anonymous `KALSHI_PUBLIC_REST`.
- Quotes older than five seconds, wrong-window books, initializing markets,
  one-sided required books, and unverified fees fail closed.

## Fees and economics

The applicable public series metadata must report `fee_type=quadratic` and
`fee_multiplier=1`. The official schedule effective 2026-07-07 defines the
general taker fee as `M * 0.07 * C * P * (1-P)`. Trade fees are rounded up to
the next centicent. For the displayed one-contract immediate fill, the
additional documented rounding fee aligns the total debit to whole cents.

The implementation records the raw trade fee, rounding fee, total fee, fee
type, multiplier, effective date, and provenance. It does not use the Webull
fixed fee. A signal requires positive point and conservative net EV and total
cost below the $1 settlement payout. This is an economic screen, not evidence
of profitability.

Official sources:

- `https://kalshi.com/docs/kalshi-fee-schedule.pdf`
- `https://docs.kalshi.com/getting_started/fee_rounding`
- `https://docs.kalshi.com/api-reference/exchange/get-series-fee-changes`

## Persistence and speech

The unchanged Step 9 shadow records continue under
`data/kalshi_forward_shadow/`. New experimental signals are deduplicated and
append-only under `data/kalshi_manual_signal/signals.jsonl`. Runtime data are
ignored by Git. The signal ledger has no execution or trade-journal meaning.

Speech is emitted only for a newly persisted primary signal and says
“experimental signal” and “manual entry only.” It never says buy, enter,
purchase, order submitted, or position opened. A stale, rolled, ambiguous, or
otherwise invalid signal disappears on the next scan and produces no speech.

Persistent UI labels state `EXPERIMENTAL — NOT YET FORWARD VALIDATED` and
`MANUAL ONLY`.
