---
number: 83
rank: 16
title: Digest and approval report count not-confirmed and broker refusals as refused by the risk check
stage: Reporting (daily digest, performance report) · RUN
size: S
status: 'proposed (BACKLOG 72 trading-safety review, round 1) · accountable: implementer · due 2026-10-24'
---
- **83. Digest and approval report count not-confirmed and broker refusals as refused by the risk check.** Value
  (RUN, owner's picture). `goldbot/telegram/digest.py:183` counts every approved card with a `gate_refusal` as "(N
  refused by the risk check)". `goldbot/ops/perf_report.py:793` (`gate_refused_after_approval`) does the same. Both
  therefore count two kinds of card that the RiskGate did not refuse:
  - item 71's MT5-box and broker refusals (`broker_refused:<retcode>`, `bridge_*`);
  - item 72's `not_confirmed` cards, whose order may exist.

  Acceptance: the digest and the report count the three causes separately, with the words from item 71's and item
  72's tables: refused by the risk check, not placed by the MT5 box or the broker, and not confirmed by the broker. A
  `not_confirmed` card resolved as "opened" counts as an opened trade. Write failing tests first in
  `tests/test_digest.py` and `tests/test_perf_report.py`.
