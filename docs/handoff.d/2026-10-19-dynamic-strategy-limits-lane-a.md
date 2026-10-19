- Dynamic per-strategy limits, lane A (ADR 0039, R71): the nightly ADR 0031 decision now also sets each model version's
  risk per trade (`Limits.strategy_risk`, why in `AdaptiveState.risk_terms`) and steps the daily cap down once when
  the 20-day median ATR15 reaches its 1-year p90. Off unless the account has `dynamic_limits` (lane C adds the field;
  read with getattr). Contract for lane B: `RiskLimitsCurrent.strategy_risk` / `risk_terms` (clamped: demo [0.25%,
  1%], elsewhere at most the default, reverted empty); `limits_profile.apply` passes `strategy_risk` to `RiskLimits`
  once gate.py has the field; intraday factors `adaptive.k_vol(vol_high)`, `k_dd(drawdown)`, `k_day(loss, allowance)`
  and `stricter_risk(a, b, base)` for the intraday-only-lower merge; `AdaptiveState.vol_high` is the regime.
  Not done here: the gate/sizing/engine wiring and API schema (lane B), the probe and the accounts flag (lane C).
