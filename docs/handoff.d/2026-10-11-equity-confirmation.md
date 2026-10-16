- Equity confirmation before the 12% kill switch (BACKLOG 44, ADR 0016, TRACEABILITY R39; revised after the
  trading-safety review of d2f2999; needs trading-safety re-review). A 12% equity read trips at once when the balance
  plus the profit and swap of every position on the account agrees within 2% of the high-water mark
  (`risk.equity_check_tolerance`). Otherwise it starts a sticky pending state: entries are refused
  `equity_unconfirmed`, no stage moves, the account is re-read on the next tick, and any later 12% read trips. Pending
  clears only after 60 s of reads below 10% (`risk.equity_recovery_s`) and survives a restart. One bad read (0 or
  collapsed) no longer closes every position at market. Exits are never gated; broker stops keep resting meanwhile.
  Bridge protocol v5 (`account_positions`, `Position.swap`): deploy the MT5 box first, then the brain (entries pause
  until both run v5). A risk-state save that fails (a full disk) on the account refresh is logged and blocks
  entries (`tick_error`) but never raises, so it can no longer stop, delay or narrow the kill switch (re-review
  MEDIUM-1, LOW-2). Follow-up: BACKLOG 53 (confirm high-water-mark rises).
