- Dynamic limits, trading-safety fixes (2026-10-19): `RiskLimitsCurrent.risk_scale` (clamped to (0, 1], default 1)
  logs the day's lowest same-day scale, and a restart the same risk day seeds `DayRisk` from it, so a recovery then a
  restart no longer lifts the cut (MEDIUM). `RiskLimitsCurrent.vol_high` carries the nightly `AdaptiveState.vol_high`
  (a decision up to a day away; kept through the 4% floors and the revert), and `k_vol` halves on it when no strategy
  term says so (LOW).
