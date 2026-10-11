- Broker ledger: closes finance audit GAP-07 and GAP-08 (row X18, ADR 0014). A new daily scheduler job,
  `broker_ledger` (23:25 UTC), only reads the broker. Per account it stores in aux.db (migration 7, additive) a
  balance, equity, margin and currency snapshot, and the deposits, withdrawals, credit and adjustments taken from the
  MT5 balance deals. It ties the balance out against the deals since the previous snapshot. It also reconciles closed
  records and open trades with the broker by position id, reporting missing, extra and mismatched positions. The
  health check `reconcile:<account>` is now the worse of the D12 feed report and the ledger report: a trade difference
  warns, an unexplained balance change fails, and the reason gives counts only. The owner reads the amounts with
  `python -m goldbot.ops.ledger show <account>`. No bridge or protocol change: `deals_since` already carries the
  balance deals. Needs trading-safety and SRE review.
