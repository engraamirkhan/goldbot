- Equity confirmation before the 12% kill switch (BACKLOG 44, ADR 0016, TRACEABILITY R39; needs trading-safety
  review). A 12% equity read trips at once when balance plus open P&L agrees within 2% of the high-water mark
  (`risk.equity_check_tolerance`); otherwise entries are refused `equity_unconfirmed`, no stage moves, and the
  account is re-read on the next tick, where a second 12% read always trips. One bad read (0 or collapsed) no longer
  closes every position at market. Exits are never gated; broker stops keep resting meanwhile.
