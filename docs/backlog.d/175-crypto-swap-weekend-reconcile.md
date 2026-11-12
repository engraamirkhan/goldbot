---
number: 175
rank: 73
title: Reconcile Vantage BTCUSD weekend swap charges against the seven-night projection
stage: 'ops + finance (follow-up of BACKLOG 153, R130)'
size: S
status: 'proposed · accountable: finance-auditor · reviews: quant-reviewer, trading-safety-reviewer'
---
- **175. Reconcile Vantage BTCUSD weekend swap charges against the seven-night projection.** Value: BACKLOG 153
  counts a crypto book's swap every server-midnight rollover with no triple day, from the item's reading of the
  broker, not from a measured charge; Vantage reports triple day 5 (Friday) for BTCUSD (`swap_rollover3days`), so
  reconcile whether Friday is also charged three nights on top of the weekend ones, or not every night is charged. Acceptance: after the first BTCUSD hold on
  vantage-demo that spans a weekend, the broker's per-position swap (deal history) is compared night by night with
  `paid_swap_per_oz` x lots x contract at the refresh price; the result is recorded in docs/finance/; the
  seven-night schedule is kept, or corrected (a triple day added, or nights dropped) with a test pinning the measured
  schedule.
