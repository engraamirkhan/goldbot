---
number: 8
rank: 11
title: Stop rule and gate thresholds in code
stage: Capital and drawdown management · RUN
size: S
status: done (owner sign-off and engine trade record open)
---

### 8. Stop rule and gate thresholds in code
Value: governance the design commits to before the first paper trade, and the safe-direction automatic halt (RUN).
Acceptance:
- A daily job computes P6 (after 18 months, more than 500 pooled trades and a lower 90% bound on expectancy below
  zero, or any single loss larger than the weekly cap). A breach sets the existing system halt. Only the owner clears
  it. Tested on synthetic trade sets on each side of each condition.
- P7: the trade-count and time thresholds for each phase gate live in `settings.yaml`. A job writes `met / not met`
  with the evidence to `state/phase_state.json`. It never flips the phase itself. Tested.
- The dashboard shows the stop-rule and gate status (API schema regenerated).

Depends on: the owner signs off the threshold values. Rows: P6, P7.

Status (2026-10-10): done in `goldbot/ops/gates_phase.py`, `run.py gates` / `gate-evidence`, health checks
`stop_rule` and `phase_gates`, `settings.yaml` `gates:`; tests in tests/test_phase_gates.py. Differences from the
acceptance above, as briefed for this change: a stop-rule breach is a health FAIL and a Telegram alert that
recommends /halt; it does not set the system halt (the RiskGate's caps are unchanged). The report goes to
`state/gate_report.json`, not into `phase_state.json`, so a report can never be read as a recorded gate. The health
pass (every 60 s) evaluates it, so no scheduler job was added. Open: owner sign-off of the PROPOSED values; the
engine appending `ClosedTrade` rows to `state/closed_trades.jsonl` (engine owner); the dashboard card (api/web
owners; the report JSON is ready to serve).
