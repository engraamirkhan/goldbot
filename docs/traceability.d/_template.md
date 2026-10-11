# Requirements traceability: docs/DESIGN.md -> code -> tests

Checked against `main` at `ea070ba` (PR 46) plus the commits on this branch, 2026-10-07. Each row is a concrete,
testable requirement from `docs/DESIGN.md` (section in brackets), its status, where it lives and the test that
proves it. Code locations are `file:line` at that revision; tests are `file::test_name`. "untested" means no test
asserts the rule, even where the code exists.

Status values:
- **implemented**: the rule is in code as the design states it.
- **partial**: some of the rule is in code; the missing part is named.
- **missing**: nothing in code enforces it.
- **deviates**: code does something different from the design's text (the reason is given when the code documents one).
- **in review**: being changed on another branch (research evaluation, proposals P1-P3 in
  `docs/proposals/2026-10-design-improvements.md`).

## Counts

<!-- traceability.d:counts -->

Fixed on this branch (failing test first, then the change): rows R6, D18 and X12 below.

Engine safety (failing test first, then the change): rows R7, R16, R17, R22, R23, R25, R26, F2, X7 and X8.

## Risk limits and RiskGate [Execution and risk: Sizing, Hard limits]

| # | Requirement | Status | Code | Test |
| --- | --- | --- | --- | --- |
<!-- traceability.d:rows R -->

Risk analytics (reporting tools, not design rows, so the counts are unchanged): `goldbot/research/ruin.py`
(`run.py ruin`, playbook G-5) simulates R against the R9/R11/R13/R14 limits read from `config/settings.yaml`, applying
them as `RiskGate` does (tests/test_ruin.py::test_stage_one_quarters_the_risk_like_the_gate_and_stage_two_halts_the_path,
::test_daily_cap_stops_the_day_and_the_next_day_starts_again); `goldbot/research/min_lot.py` (`run.py sizing-feasibility`,
G-7) reports the equity at which R5's minimum-lot rule lets a stop be sized
(tests/test_min_lot.py::test_agrees_with_the_risk_gate_on_whether_the_minimum_lot_is_allowed). Both size through
`goldbot/risk/sizing.py`, the R2/R4/R5/R6/R14 arithmetic as pure functions, held equal to `RiskGate` by
tests/test_sizing_parity.py::test_sizing_module_matches_the_gate and ::test_stage_machine_matches_update_stage.

## Supervisor [Execution and risk: Supervisor]

| # | Requirement | Status | Code | Test |
| --- | --- | --- | --- | --- |
<!-- traceability.d:rows S -->

## Approval flow and Telegram [Operating mode, Telegram rights]

| # | Requirement | Status | Code | Test |
| --- | --- | --- | --- | --- |
<!-- traceability.d:rows A -->

## Dashboard authentication [Infrastructure: Dashboard]

| # | Requirement | Status | Code | Test |
| --- | --- | --- | --- | --- |
<!-- traceability.d:rows U -->

## Data and time [Data architecture]

| # | Requirement | Status | Code | Test |
| --- | --- | --- | --- | --- |
<!-- traceability.d:rows D -->

## Features and labels [Features and labels]

| # | Requirement | Status | Code | Test |
| --- | --- | --- | --- | --- |
<!-- traceability.d:rows F -->

## Modelling, validation and promotion [Modelling]

| # | Requirement | Status | Code | Test |
| --- | --- | --- | --- | --- |
<!-- traceability.d:rows M -->

## Execution and broker [Broker abstraction, Order lifecycle, Reconciliation, Account classifier]

| # | Requirement | Status | Code | Test |
| --- | --- | --- | --- | --- |
<!-- traceability.d:rows X -->

## Phases, accounts and credentials [Demo first, live by gate; Logins and credentials]

| # | Requirement | Status | Code | Test |
| --- | --- | --- | --- | --- |
<!-- traceability.d:rows P -->

## Population and agents [Evolving the system]

| # | Requirement | Status | Code | Test |
| --- | --- | --- | --- | --- |
<!-- traceability.d:rows G -->

## Account view and monthly report [ADR 0004 sections 3, 4 and 6; BACKLOG 26; BACKLOG 23]

| # | Requirement | Status | Code | Test |
| --- | --- | --- | --- | --- |
<!-- traceability.d:rows V -->

## Gaps

Fixed on this branch:
- R6 per-trade risk clamp after the multiplier (goldbot/risk/gate.py).
- D18 non-monotonic ingest check could never fire (goldbot/data/quality.py).
- X12 classifier read zero-commission accounts with tight spreads as Raw (goldbot/execution/classifier.py).
- Tests pinning the design numbers: tests/test_trace_risk.py, tests/test_trace_data_exec.py.

Already fixed on `main` by other work and verified here: settings-driven RiskLimits/SupervisorLimits (R1, S1),
risk-day/week roll (R10, R12), kill switch closing at market (R15), owner re-arm (R16), dq_error wiring (R30),
phase-gate recording (P4), store dedupe and `asof_join` (D14), stable magic numbers (X3), exact lot-step rounding (R4).

In review on another branch (research evaluation, proposals P1-P3): M12, M17, M18, M19, M31, M32.

Larger gaps, by priority (effort: S < 1 day, M 1-3 days, L > 3 days):

1. ~~X7 persisted `pending_orders` and restart reconciliation against deals~~ (done).
2. ~~X8 reconciliation stops~~ (done).
3. ~~R22/R23 weekend and rollover rules~~ (done).
4. ~~R7 combined exposure cap~~ (done).
5. ~~X14 act on the account class~~ (done).
6. ~~R17 combined 8% stage~~ (done).
7. ~~F2 feature-version check at scoring~~ (done).
8. ~~R25/R26 stale data~~ (done).
9. ~~R16 re-arm probation~~ (done).
10. ~~M3/M6/M7 live exit policies~~ (done; M3 15m execution and M6 stop inside range remain).
11. ~~D22 quarantine of error batches~~ (done).
12. ~~M26/M27 drift and health~~ (done).
13. ~~A10 auto-mode offer~~ (done).
14. ~~R21 blackout early close if p < 0.5~~ (done).
15. **M28 TreeSHAP per decision**, and A5 per-trade SHAP in proposals (M).
16. **F10/D23 Dukascopy cross-feed check**, **D20 spike other-feed match** (M).
17. **M16 combinatorial purged CV quarterly** (M); **M10 learned allocator** after three months (L).
18. **F12 TradingView feature family** with NaN handling and 1% blanking (L); **D4 swap-day and swap costs** (M).
19. ~~P6/P7 stop rule and gate thresholds in code~~ (evaluated in `goldbot/ops/gates_phase.py`; left: owner sign-off of
    the PROPOSED thresholds and the engine writing `state/closed_trades.jsonl`).
20. Minor: ~~**A2** engine window from settings~~ (done), **M15** research callers pass settings to `splits_for` (it accepts
    them now), ~~**G6** DSR >= 0.95~~ (done), **U4**
    the PROPOSED thresholds; the engine now writes `state/closed_trades.jsonl`).
20. Minor: ~~**A2** engine window from settings~~ (done), **M15** walk-forward reads settings, **G6** DSR >= 0.95,
    **U4** lock measured from the fifth failure, **M25** CUSUM calibrated to 5% quarterly false alarms, ~~**D10** bar
    close by clock + 1.5 s grace~~ (done).
