# Phase 10 — Failure Recovery

MANTIS remains observation-only and fail-closed. Presentation failure never
changes a decision; persistence failure stops observation rather than claiming
that an unrecorded scan was preserved.

## Recovery matrix

| Failure | Runtime behavior | Restart behavior |
|---|---|---|
| browser closed | SSE disconnect unregisters; scanner continues; no relaunch loop | next operator start creates one app window |
| web bind/server failure | interface reports disabled; scanner remains independent | ephemeral port selection normally avoids conflict |
| provider timeout | bounded exponential retry; cache is labelled; unavailable data becomes DATA HOLD | next scan retries normally |
| economics exception | provider chain falls through; economics stays unavailable | no invented quote or EV |
| forward write/fsync failure | DATA HOLD, diagnostic record attempt, scanner stops safely | operator fixes disk/lock and reruns health check |
| truncated final JSONL row | final partial row is ignored, never rewritten | deterministic IDs prevent duplicate entry |
| malformed interior JSONL row | startup fails closed with file and line | preserve file; restore from backup/manual repair |
| audio/voice failure | bounded queue, failure counter, subsystem disables | scanner and records are unaffected |
| Ctrl+C | reporter joins; server/SSE close; audio/voice stop; owned browser closes | append-only files remain intact |

Raw forward files are never rotated or deleted automatically. Only the separate
technical diagnostics log rotates, at 2 MB by default with three backups.

OneDrive locking cannot be eliminated by application code. MANTIS treats an
`OSError` during persistence as unsafe, stops scanning, and reports `CHECK
REQUIRED`; it does not continue with an unrecorded state.
