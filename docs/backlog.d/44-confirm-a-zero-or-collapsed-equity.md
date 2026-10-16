---
number: 44
rank: 4
title: Confirm a zero or collapsed equity read before the 12% kill switch (ADR 0016)
stage: Execution and risk (drawdown stages) · PROTECT
size: S
status: 'in review (d2f2999, b9e9dac; re-review 2026-10-16 found a save-failure gap, fixed in 869bb04; all in PR #79; trading-safety re-verify of 869bb04 not on record) · accountable: trading-safety-reviewer · due 2026-10-17'
---
- **44. Confirm a zero or collapsed equity read before the 12% kill switch (trading-safety re-verification LOW-C,
  2026-10-11; needs a decision record and trading-safety review).** Value: one bad `account().equity` read (0, or a
  value far below the last) moves `update_stage` straight to HALTED, and the kill switch closes every position at
  market. The gate now refuses an entry on equity <= 0 without running the stage machine. The engine's own stage
  update in `_refresh_account` still trusts a single read.
  Acceptance:
  - before a stage-2 transition, the engine cross-checks the equity against the balance and its open P&L, or takes a
    second read; one disagreeing read never trips the kill switch, and two agreeing reads always do;
  - an ADR in `docs/decisions/` records the rule, how long a confirmation may take, and why that delay is safe while
    the broker's stops are resting;
  - tests: a single zero-equity read never closes a position; two consecutive real 12% reads close everything as
    today.
  - **Done 2026-10-11:** ADR 0016; `RiskSettings.equity_check_tolerance`, `AccountState.equity_unconfirmed`,
    runner `_equity_unconfirmed` (re-read on the next tick), gate reason `equity_unconfirmed`; tests in
    `tests/test_equity_confirmation.py` (TRACEABILITY R39).
  - **Review fixes 2026-10-12 (trading-safety review of d2f2999):** HIGH-1 pending is sticky until 60 s below 10%
    (`risk.equity_recovery_s`); MEDIUM-2 the cross-check counts swap and every symbol (`account_positions`, bridge
    protocol v5); LOW-4 pending persisted in the risk state; LOW-3 declined with a test (ADR 0016). Follow-up: 53.
    Awaiting trading-safety re-review.
  Sweep 2026-10-16 (product owner): accountable `trading-safety-reviewer`, due 2026-10-17. Built (TRACEABILITY R39, ADR 0016); the re-review asked on 2026-10-12 has not started (4 days against the 4 h rule). Acceptance: the re-review records HIGH-1, MEDIUM-2 and LOW-3/LOW-4 closed, `tests/test_equity_confirmation.py` green, then it ships with the reviewed batch (release-manager).
