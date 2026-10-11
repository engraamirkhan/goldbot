- Broker ledger: closes finance audit GAP-07 and GAP-08 (row X19, ADR 0015). A new daily scheduler job,
  `broker_ledger` (23:25 UTC), only reads the broker. Per account it stores in aux.db (migration 7, additive):
  - a balance, equity, margin and currency snapshot, stamped with the real read time and keyed by the slot's day;
  - the deposits, withdrawals, credit and adjustments taken from the MT5 balance deals. A baseline day books them
    flagged `baseline`; operations after a cancelled deal are tagged `follows_cancel`.

  It ties the balance out against the deals after the previous snapshot. The deal cursor stays within a day of the
  read, so an idle account never drifts into a new baseline. An unexplained balance is read again once, 5 s later,
  before it FAILs. It also reconciles closed records and open trades with the broker by position id, reporting
  missing, extra and mismatched positions.

  The health check `reconcile:<account>` is now the worse of the D12 feed report and the ledger report: a trade
  difference warns, an unexplained balance change fails, any aux.db error other than a missing table warns, and the
  reason gives counts only. The owner reads the amounts with `python -m goldbot.ops.ledger show <account>`
  (read-only). No bridge or protocol change: `deals_since` already carries the balance deals.
