---
number: 175
rank: 12
title: Reconcile Vantage BTCUSD weekend swap charges against the seven-night projection
stage: 'ops + finance (follow-up of BACKLOG 153, R130)'
size: S
status: 'implemented, finishes itself (2026-11-14, branch feat/auto-gated-1114, R152): trigger: the daily swap_reconcile job (23:45 UTC) after any weekend BTCUSD hold closes stores the broker swap against the seven-night projection (aux.db swap_reconciliations); health swap_reconcile warns past costs.swap_reconcile_tolerance; the finance-auditor keeps or corrects the schedule on that warn · accountable: finance-auditor · reviews: quant-reviewer, trading-safety-reviewer'
---
- **175. Reconcile Vantage BTCUSD weekend swap charges against the seven-night projection.** Value: BACKLOG 153
  counts a crypto book's swap every server-midnight rollover with no triple day, from the item's reading of the
  broker, not from a measured charge; Vantage reports triple day 5 (Friday) for BTCUSD (`swap_rollover3days`), so
  reconcile whether Friday is also charged three nights on top of the weekend ones, or not every night is charged. Acceptance: after the first BTCUSD hold on
  vantage-demo that spans a weekend, the broker's per-position swap (deal history) is compared night by night with
  `paid_swap_per_oz` x lots x contract at the refresh price; the result is recorded in docs/finance/; the
  seven-night schedule is kept, or corrected (a triple day added, or nights dropped) with a test pinning the measured
  schedule.
