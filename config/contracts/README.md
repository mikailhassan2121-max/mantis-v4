# Manual contract specifications

Drop `.json` or `.csv` files here to give MANTIS a **real** contract reference
and **real** quotes without needing API credentials. `LocalFileContractProvider`
re-reads this directory whenever a file's modification time changes, so you can
update quotes mid-window and MANTIS picks them up on the next scan.

## Why this exists

Without it, MANTIS falls back to `UnderlyingProxyContractProvider`, which uses
the first 1-minute Open of the window as an **unverified proxy** for the strike.
That proxy can never unlock EV calculations, by design (Phase 1 audit, finding
E-1). This directory is the credential-free path to a real reference.

## Important: manual is not verified

A reference you type here is recorded as `MANUAL_LOCAL`, which is **not**
treated as verified. The UI will still show `PROXY / UNVERIFIED` for the
reference source unless you also declare an explicit `settlement_rule`.

Declaring a settlement rule is your attestation that you read it off the real
contract specification. That speed bump is deliberate: "I told MANTIS the
strike" and "MANTIS verified the strike" must remain visibly different things.

## JSON format

```json
{
  "contracts": [
    {
      "asset": "BTC-USD",
      "window_start_utc": "2026-08-13T15:00:00Z",
      "reference_price": 63400.0,
      "settlement_rule": "TERMINAL_ABOVE_REFERENCE",
      "venue_contract_id": "optional-venue-id",
      "yes_bid": 0.54,
      "yes_ask": 0.55,
      "no_bid": 0.44,
      "no_ask": 0.46,
      "quote_timestamp": "2026-08-13T15:04:11Z"
    }
  ]
}
```

CSV uses the same column names, one row per contract.

## Rules

- **Every timestamp must be timezone-aware.** `2026-08-13T15:00:00Z` or
  `2026-08-13T11:00:00-04:00`. A naive timestamp is rejected, not assumed to be
  UTC or local — assuming would be a silent wrong-answer path (audit D-8).
- `window_start_utc` is floored to the containing 15-minute window, so any
  instant inside the window works.
- Valid `settlement_rule` values: `TERMINAL_ABOVE_REFERENCE`,
  `TERMINAL_AT_OR_ABOVE_REFERENCE`, `UNKNOWN`. Omit it if you do not know it —
  do not guess, because it determines the sign of every recorded outcome.
- Prices must be in `[0, 1]` dollars per contract. A quote failing the sanity
  check is refused rather than repaired.
- Quotes go stale. `max_quote_age_seconds` (default 15s) governs whether a
  quote is fresh enough to support EV. A stale quote disables EV; it is never
  extrapolated.

Files in this directory are gitignored except this README and
`example_contract.json`.
