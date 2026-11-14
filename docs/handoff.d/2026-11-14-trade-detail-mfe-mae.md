- 2026-11-14 (branch feat/cleanup2-mfe-ui-1114; R155; BACKLOG 243): the trades API's `TradeRow` carries a closed
  exploration or control row's MFE / MAE from its R135 record (`mfe_r`, `mae_r`, `mfe_usd`, `mae_usd`,
  `excursion_approx`; None on older rows and every other kind). The trade detail's "Details" disclosure shows them
  for a closed arm trade ("for +1.40 R (+4.20 USD/oz) · against −0.60 R ..."), "not recorded" when the record has
  none (never 0 R) and "approximate" when part of the path came from the 1m bars. API contract regenerated. Read-only;
  nothing on the order path changed.
