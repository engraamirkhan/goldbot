- 2026-11-13 (branch feat/gold-only-demo-mfe; R135; research-director ruling 2026-11-13, gold-only demo): every
  non-gold arm and control (89 rows: S0-/I0-/LB-/LBC-<instrument>) and IEX1H retire on 2026-11-14 on their frozen
  rows (`arms.GOLD_ONLY_RULE`; id, hash and K unchanged). The accounts.yaml registrations stay so open positions keep
  every exit (dropping them once flat is backlog 187). Still entering: 24 gold rows (S0, S0R, S1A/B, Z1P, Z2P, Z5P,
  Z3D, Z9D, Z10D, Z3V, Z5V, WKS0, WKI0, GS1, GS1C, LCZ*, VEX15, VEX1H). icm-probe's intraday pot is left with no arm
  that may enter, on purpose. No new 1m arm: `load_arms` refuses a 1m row whose id is not on
  `arms.ONE_MINUTE_ALLOWED`, so a new one needs a dated ruling. A2 readability no longer retires a 1h+ arm from
  2026-11-13 (`load_arms` refuses that retirement); the 2026-10-30 A2 rows stand. A2 had no code: it is applied by
  hand as a `retired_rule`, so the guard sits on the spec. Each closed `exploration_trades` body now carries
  `mfe_r`/`mae_r`/`mfe_usd`/`mae_usd` (USD per oz) and `excursion_approx`, taken from the closed-trade record's tick
  track; older rows read None. Reading them in reports is backlog 195. No exit, cap, RiskGate rule or size changed.
