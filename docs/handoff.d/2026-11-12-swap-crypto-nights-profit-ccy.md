- 2026-11-12 (branch fix/swap-crypto-nights-profit-ccy; BACKLOG 153, 154; R130): a crypto book (registry asset class
  crypto, Vantage BTCUSD) now projects swap for every server-midnight rollover, weekends included, with no triple day
  (`SwapSpec.seven_night`, set by `books.swap_spec`; `rollover_nights(..., seven_night=True)`); a Friday-night entry
  held to Monday is charged Saturday and Sunday instead of nothing. Gold and every other book are unchanged; the
  labels and the CostImputer share the same count. No research golden moves: every research path prices gold (flag
  off), and the strategy catalogue already counts crypto per calendar night.
- BACKLOG 154, scope corrected from the item: converting the swap into USD in `books.swap_spec` would have charged it
  twice, because the RiskGate already reads every price amount of a non-USD book (`atr_usd`, the stop,
  `swap_usd_per_oz`) in its profit currency and converts it with `acct_per_profit` (`profit_fx`), unknown refusing
  `fx_conversion_unavailable` (pinned by new hand-worked DE40 EUR mode 3 and JPY mode 5 gate tests). So mode 5/6 is now
  priced in any reported profit currency (it stays in that currency, like modes 1-3); an unreported one stays
  `swap_unknown`, and `books.swap_spec` refuses (swap unknown, entries refused) when the terminal's profit currency
  is not the registry's the gate converts from. RiskGate unchanged.
- Follow-up: BACKLOG 175 (finance-auditor reconciles the first weekend BTCUSD hold against the seven-night projection).
