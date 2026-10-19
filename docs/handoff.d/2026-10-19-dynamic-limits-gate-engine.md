- Dynamic limits, lane B (gate + engine; docs/design/dynamic-limits-2026-10-19.md): `RiskLimits.strategy_risk` (risk
  per trade by model version, clamped to the 1% maximum; unknown or corrupt -> the base rate) and `risk_scale`
  (k_vol x k_dd x k_day, in (0, 1], also cuts the minimum-lot exception cap); `Intent.model_version` (runner passes
  `live_versions`); `sizing.effective_risk(..., risk_frac=)`; the allowed `GateDecision` carries `risk_per_trade` and
  `risk_source` while a dynamic term is in force. engine/limits_apply.py `DayRisk`: r_s only loosens at the risk-day
  roll, the day's lowest scale holds, a restart the same day starts from the logged r_s, live/no-ceiling accounts are
  capped at the default rate; journal `risk_terms_change` and `limits_change.risk_terms`; engine file `risk_today`;
  `GET /api/limits` `risk_today` ("why this limit today"). Off unless the account has `dynamic_limits: true` (lane C):
  with it off the gate grid, the engine file and every decision are unchanged. Seams read by name until lanes A/C
  land: `RiskLimitsCurrent.strategy_risk`/`risk_terms`, `AdaptiveState.strategy_risk`, `risk_terms[v]["k_vol"]`,
  `Account.dynamic_limits`. Not done: `EntryAudit.risk_frac`/`risk_source` (goldbot/ops/gates_phase.py, outside the
  lane). Rollback: `dynamic_limits: false` and restart.
