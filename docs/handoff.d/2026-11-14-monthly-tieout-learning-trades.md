- 2026-11-14 (branch fix/monthly-tieout-learning-trades; issue #394): the monthly review's broker tie-out now counts
  exploration, control and cost-probe closes (aux.db, via `learning_ledger.closed_learning_trades`, matched by
  `account_name`) in the P&L, the trade count and the per-trade tolerance; before, every demo account tied out
  against zero trades. Rows voided as evidence still count as money (`include_excluded=True`; the evidence readers
  are unchanged). An unreadable trade ledger and a balance snapshot without a login fingerprint (`identity`: a shared
  login cannot be ruled out, as before 2026-10-26) are tie-out reasons, never a silent pass. The monthly report reads
  the same rows on their own line (never in the return, R, drawdown or days), so an exploration-only account has a
  report. Months before 2026-10-26 will stay flagged on the identity reason by design. Reporting only: the daily
  ledger, RiskGate, engine and order code are untouched.
