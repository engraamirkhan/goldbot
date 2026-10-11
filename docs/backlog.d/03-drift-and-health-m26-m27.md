---
number: 3
rank: 6
title: Drift and health (M26/M27)
stage: Learning and adaptation · RUN
size: S
status: 'done (PR #60)'
---

### 3. Drift and health (in review, PR #60)
Value: halts or sizes down a decaying champion the moment one exists (RUN).
Acceptance: CI green on PR #60. Tests cover the PSI and ECE size-down, residual CUSUM halt and system halt.
`run.py drift-review --clear` is the only path that clears a halt. TRACEABILITY M26/M27 read implemented.
Rows: M26, M27. M25 (CUSUM calibration) stays under "later".
