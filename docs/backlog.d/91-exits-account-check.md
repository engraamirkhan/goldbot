---
number: 91
rank: 27
title: Exits, stop changes and the kill switch check the terminal account
stage: Execution · PROTECT
size: S
status: 'proposed (raised by the trading-safety review of the per-order account check, 2026-10-19; severity LOW) · accountable: implementer · reviews: trading-safety-reviewer'
---
- **91. Exits, stop changes and the kill switch check the terminal account.** Value: entries are now refused when the MT5 terminal is no longer on the registered account (direct adapter `MT5Broker(verify=...)` and the bridge's GuardedBroker), but `close`, `modify`, reconciliation (orphan adoption, reinstated stops) and the 12% kill switch's "close everything at market" are never checked, on both paths. If the terminal is switched to another account, the engine could close, re-stop or adopt that account's positions (manual trades on a real account included). Severity LOW: it needs a terminal switched while the engine runs, and exits must never be blocked for goldbot's own trades. Acceptance: (1) a close or stop change of a position the engine recorded itself (ticket in its open trades or pending orders, magic in the account's range) is never gated, whatever the terminal reports (test); (2) on a terminal that fails `accounts.verify_terminal_account`, the kill switch's close-all, orphan adoption and stop reinstatement act on no position the engine did not record, and journal plus alert once per streak instead (tests on both the direct adapter and GuardedBroker); (3) an unreadable account counts as failing for (2) only, never for (1); (4) trading-safety-reviewer signs off that no exit of goldbot's own trades can be blocked.
