---
number: 62
rank: 8
title: The 12% kill switch flattens every position on the account, all symbols
stage: Execution and risk (drawdown stages, ADR 0016) · PROTECT · RUN
size: S
status: 'ready (decided 2026-10-16) · accountable: implementer, then trading-safety-reviewer · due 2026-10-22 (built 10-21, trading-safety verdict 10-22)'
---
- **62. The 12% kill switch flattens every position on the account, all symbols.** Decided 2026-10-16 by program
  director + product owner (owner delegated). Value (RUN, protect): the 12% drawdown is measured on the account's
  equity, which already counts every symbol's P&L (ADR 0016 cross-check, `account_positions`), yet the trip closes
  only XAUUSD (`Engine._kill_switch` reads `broker.positions()`, the engine's symbol). A losing position on another
  symbol keeps draining the account after the halt. Closing everything matches what the switch measures. This
  tightens risk; it loosens nothing, so it is a delegated decision. It supersedes the "other symbols untouched, an
  owner decision" line of the F1 fix (handoff 2026-10-16-kill-switch-everything-sticky). TRACEABILITY R13, R39.
  Depends on: nothing; touches goldbot/engine/runner.py `_kill_switch`, the broker close path
  (goldbot/execution/mt5_adapter.py, bridge, paper), docs/decisions/0016, docs/RUNBOOK.md. Starts after lane A's 59
  re-verify clears runner.py (same file), not before the Sunday engine start.
  Acceptance:
  - on the trip (`everything=True`, and while `_kill_everything` is set) the switch reads
    `broker.account_positions()` and closes every position on the account, every symbol and every magic; the sticky
    flag clears only when `account_positions()` reads flat (not `positions()`);
  - each non-XAUUSD close uses that position's own symbol: its own tick, filling mode and volume step (today
    `_filling` reads `self.symbol`); one symbol's refused or market-closed close never stops the others and is
    re-sent by `_retry_closes` with the existing backoff;
  - an unreadable `account_positions()` falls back to closing what `positions()` returns and keeps the switch pending
    (never fewer closes than today);
  - after the whole account is seen flat, later halted refreshes still close only this engine's magic range, as
    today;
  - ADR 0016 is amended (all symbols, why, the manual-trade consequence); RUNBOOK section on the 12% kill switch
    says in plain words: "if the kill switch trips, every open position on the account is closed at market,
    including trades you opened by hand on other symbols";
  - tests (tests/test_kill_switch_everything.py): a EURUSD and an XAUUSD position both close on the trip; a refused
    EURUSD close is retried and does not block the XAUUSD close; the flag stays set until both are gone; after a
    restart the saved flag still closes the EURUSD position; paper and bridge brokers close a non-engine symbol;
  - trading-safety-reviewer APPROVE recorded at the merge (E1), with sha and date.
