- 2026-11-13 (branch feat/gold-only-demo-mfe; R135; docs/research/ruling-2026-11-13-gold-only.md): every non-gold
  arm and control (89 rows: S0-/I0-/LB-/LBC-<instrument>) retires on 2026-11-14 on its frozen row
  (`arms.GOLD_ONLY_RULE`; id, hash and K unchanged). The accounts.yaml registrations stay so open positions keep every
  exit (dropping them once flat is backlog 187). Still entering: 25 gold rows (S0, S0R, S1A/B, Z1P, Z2P, Z5P, Z3D,
  Z9D, Z10D, Z3V, Z5V, WKS0, WKI0, GS1, GS1C, LCZ*, VEX15, VEX1H, IEX1H; the quant review kept IEX1H, icm-demo's only
  1h cost stream). icm-probe's intraday pot is left with no arm that may enter, on purpose. No new 1m arm: a 1m row
  whose id is not on `arms.ONE_MINUTE_ALLOWED` is refused. A2 readability (applied by hand as a `retired_rule`, the
  token A2 anywhere) no longer retires a 1h+ arm from 2026-11-13; the 2026-10-30 A2 rows stand. Both rules raise in
  the research and CI loaders (`load_arms` with the hash check). On the engine path the row stays configured with its
  entries refused and its exits managed, and a discovered arm (spec_arms.yaml) is left out and named; a stray row never
  stops an engine (trading-safety review). Each closed `exploration_trades` body now carries `mfe_r`/`mae_r`/
  `mfe_usd`/`mae_usd` (USD per oz) and `excursion_approx`, taken from the closed-trade record's tick track. Any failure
  leaves those fields empty and the row is still written; older rows read None. Reading them in reports is backlog 195.
  No exit, cap, RiskGate rule or size changed.
