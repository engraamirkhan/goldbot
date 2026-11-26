- 2026-11-26 ADR 0106 decision 2: the owner's real-money limits are in the RiskGate as code constants
  (`goldbot/risk/real_money.py`, `Final`, no settings key): 1 % a trade with its round trip (`rm_risk_per_trade`), each
  entry sized down to what is left of the day's 2 % after the loss so far and the open risk (`rm_daily_projected` below
  the minimum lot; open risk must be known), a 2 % day latched to the risk-day roll and kept across restarts (`rm_daily_stop`), a halt at min(dd_stage2, 15 %)
  (`rm_drawdown_halt`), and an order within 1 % of the instrument's reference daily volume (`rm_volume`; unknown refuses
  `rm_volume_unknown`). They apply after every profile, override, dynamic term and pilot rule on a real-money account
  (`RiskLimits.real_money`, set by `engine_limits` for a live account's real terminal); demo is unchanged.
  - The ADR 0055 pilot block is bounded by them at load (max_risk_frac <= 0.01, daily_cap_frac <= 0.02, stages always
    on; icm-live now 0.01 / 0.02 / true). At 1 % the £50 pilot cannot trade (needs about £280); ADR 0055 amended.
  - `config/data/adv_reference.yaml` is a placeholder (XAUUSD `adv_notional: null`): every real-money entry is refused
    until the data engineer fills it with source and date (finding ADV-REFERENCE-XAUUSD). No live account is enabled.
  - Trading-safety review of #491: real money also by the terminal's own report (`AccountState.terminal_trade_mode`,
    a real login under a demo id gets every limit and no ADR 0097 freeze); the day latches only on two agreeing
    comparable account reads; the ADV reference's age is judged at every entry; on real money the sizing target leaves
    room for the round trip (1 % is a ceiling, not a refusal); the pilot weekly cap is back to 5 %; an engine start
    without the limits on a live or real-terminal account forces them on and winds down (exits run). RUNBOOK: withdraw
    from a real-money account only when flat, then re-arm. Rollback floor for live hosts: finding ADR0106-ROLLBACK-FLOOR.
