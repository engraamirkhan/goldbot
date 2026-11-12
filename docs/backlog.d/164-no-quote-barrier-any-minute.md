---
number: 164
rank: 67
title: No-quote time barrier counts bars with any expected-open minute
stage: 'engine (quant review of backlog 161, 2026-11-12)'
size: S
status: 'proposed · accountable: implementer · reviews: quant-reviewer, trading-safety-reviewer'
---
- **164. No-quote time barrier counts bars with any expected-open minute.** Value: the dead-feed time barrier (`Engine._open_bars`, runner.py ~800) counts a decision bar only when its start is open (`sessions.is_open(starts)`), while the downtime catch-up (engine/downtime.py, backlog 161) counts a bar when any of its minutes is in `SessionTable.expected_open` (exchange holidays closed). The bar that opens after the daily break (break end 01:02 server inside a 01:00 bar) is not counted by the first rule, and a CME holiday is counted by it; the two paths can disagree on the same hold. Acceptance, tests first: `_open_bars` uses `downtime.missed_closes`' rule (any expected-open minute; moved out of runner.py, ADR 0023, lowering LINE_CAP), with tests for a bar straddling the daily break and for a CME holiday; exits only get earlier versus the label, never later; the replay golden is unchanged (a replay never runs the priceless path).
