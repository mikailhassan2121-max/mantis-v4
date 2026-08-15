# Phase 11 — Operator Guide

Run live collection with `start_mantis.bat` or `python mantis_v4_live.py`.

Read-only reporting commands:

```powershell
python mantis_v4_live.py --forward-report
python mantis_v4_live.py --daily-report
python mantis_v4_live.py --daily-report 2026-08-15
python mantis_v4_live.py --forward-manifest
python mantis_v4_live.py --audit-contract CONTRACT_ID
python mantis_v4_live.py --incorrect-report
python -m mantis_v4.forward.audit CONTRACT_ID
```

Reports do not start scanning or write evidence. `WEBULL AUTH NOT CONFIGURED` does not block
classification collection. MANTIS remains observation-only with no order execution path.
