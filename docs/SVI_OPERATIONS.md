# SVI operator runbook

## Read-only inspection

Run these from the repository root:

```text
python mantis_v4_live.py --svi-capabilities
python mantis_v4_live.py --svi-audit
python mantis_v4_live.py --svi-manifest
python mantis_v4_live.py --svi-report
python mantis_v4_live.py --svi-backtest
```

All paths pass through the existing MANTIS scope protections. These commands do
not start live scanning or write evidence.

## Interpreting states

- `PASS`: the evidence ledger satisfies structural and manual-only invariants.
- `INSUFFICIENT_EVIDENCE`: continue shadow observation; do not promote.
- `NOT_ELIGIBLE`: one or more explicit admission gates failed.
- `ELIGIBLE_FOR_HUMAN_REVIEW`: evidence cleared automated checks, but no role
  changed and no model was activated.
- `ALERT`: recent forecast behavior deteriorated or shifted beyond the fixed
  drift threshold. Investigate data provenance and policy changes.

## Recovery

- A truncated final JSONL row is tolerated; malformed interior rows fail audit.
- Duplicate event identifiers are rejected by the append sink and audit.
- A specialist exception is isolated and recorded under `agent_errors`.
- SVI evidence/report failures degrade the SVI panel but do not alter MANTIS's
  authoritative selection, policy versions, or memory.

## Promotion procedure

There is no automated promotion command. Review the audit, manifest, resolved
report, historical replay, per-asset scorecards, drift report, and lifecycle
history. If review is approved, change the specialist role explicitly in code,
review the diff, and rerun the complete test and safety gate. Never edit historical
evidence to justify a transition.
