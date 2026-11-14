- 2026-11-14 (branch fix/monthly-tieout-learning-trades; issue #394): the monthly review's broker tie-out now counts
  exploration, control and cost-probe closes (aux.db, via `learning_ledger.closed_learning_trades`, matched by
  `account_name`) in the P&L, the trade count and the per-trade tolerance; before, every demo account tied out
  against zero trades. Rows voided as evidence still count as money (`include_excluded=True`; the evidence readers
  are unchanged). An unreadable trade ledger and a balance snapshot without a login fingerprint (`identity`: a shared
  login cannot be ruled out, as before 2026-10-26) are tie-out reasons, never a silent pass. The monthly report reads
  the same rows on their own line (never in the return, R, drawdown or days), so an exploration-only account has a
  report. Months before 2026-10-26 will stay flagged on the identity reason by design. Reporting only: the daily
  ledger, RiskGate, engine and order code are untouched.
  Review round (quant): the per-trade tolerance now grows with sqrt(trades), not linearly (per-row rounding spreads
  as sqrt(n); a linear allowance at thousands of learning trades a month would hide real money). The digest's month
  line reads no learning rows and shows only accounts with phase-gate trades. The report's learning line says
  "account money, not evidence" with the voided and censored counts; an account with no phase-gate trades says so
  instead of zero figures; a learning row on an account the config does not name is an error, not a new account.
