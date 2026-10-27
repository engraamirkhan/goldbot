- **Live pilot rails (ADR 0055, R82), icm-live still disabled.** `icm-live`: server `ICMarketsSC-MT5-3`, currency
  GBP (another currency on the terminal is refused), `server_tz: NY+7`, login null, `daily_loss_limit_abs: 6.25`,
  `approval_mode: propose`, and its `pilot:` block: 0.01 lot, stops <= $4 (refused, never shrunk), <= £3.12 a trade
  with the round trip, one position, 15m only, entries halt for good at £25 realised loss or 30 filled entries
  (counted from the broker's deals, kept in core.db `pilot_state`, unreadable = entries refused), the £25 also
  projected before every entry, gate `leverage_cap` 500 (the terminal's leverage when lower), bridge notional
  ceiling 150x equity on this account only, weekly cap 25% (`weekly_cap_frac`) and every position closed at the
  Friday cut, winners too (`weekend_flat`), per the ADR 0055 amendment. Health measures its server clock as soon as
  its bridge answers.
