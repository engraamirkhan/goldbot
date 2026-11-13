---
number: 75
title: 'E-2: discovery threshold from the inner cross-fitted top-tercile cut (Q1 2027 pre-registration)'
stage: Research (discovery pipeline, pre-registration E-2) · LEARN
size: M
status: 'done (triage 2026-11-13): PR #88 (bcef2064, 93a78914)'
---
- **75. E-2: discovery threshold from the inner cross-fitted top-tercile cut (Q1 2027 pre-registration).** Value: the Q1 2027 pre-registration (O-4/O-5/O-6, H-14 15m discovery) trades `p >= the top-tercile cut`, but the code only implements calibrated p > break-even + 0.02 (`pipeline.py:115`, `cpcv.py:15`, `discovery.reading_rule`); at 15m costs that rule can cut retention to ~0.9% and void the power claims. Acceptance: the cut is taken from inner cross-fitted p (never in-sample training p); a test proves the cut never sees test-fold rows; each pre-registered row names its threshold rule; thresholds are never changed to reach 500 trades.
