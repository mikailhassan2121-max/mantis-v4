# Phase 11 — Statistical Reporting

Primary evaluation uses one immutable entry-time confidence per entered asset/contract. Accuracy
uses a bootstrap interval clustered on the shared 15-minute window. Fewer than 25 resolved entries
or 10 distinct windows is labelled `SAMPLE TOO SMALL` and suppresses the interval.

Reports include coverage, YES/NO, asset, confidence, fragility, entry-time, reference provenance,
provider quality, calibration, Brier score and log loss. Repeated valid observations are reported
separately and never presented as independent entry evidence.

Drift states are `INSUFFICIENT SAMPLE`, `WITHIN EXPECTED RANGE`, `WATCH`, and `MATERIAL
DEVIATION`. WATCH requires 50 resolved entries; material deviation requires 100. Milestones at
25, 50, 100, 250, 500 and 1,000 are review points only, never retuning triggers.
