# Phase 11 — Data Integrity

`data/forward/` remains append-only. Phase 11 adds `window_events.jsonl` and
`session_events.jsonl`; older schemas are not rewritten. The manifest reports row counts, schema
versions, policy versions and SHA-256 hashes. A truncated final append remains recoverable;
malformed interior lines fail closed.

Future policy changes require a new identifier and separate analysis. Losses, holds and provider
failures must never be removed. Resolutions never update observations or entries.

Safe local PowerShell backup while MANTIS is stopped:

```powershell
$stamp = Get-Date -Format yyyyMMdd-HHmmss
Copy-Item -LiteralPath data\forward -Destination "data\forward-backup-$stamp" -Recurse
```
