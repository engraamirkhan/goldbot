---
number: 44
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
