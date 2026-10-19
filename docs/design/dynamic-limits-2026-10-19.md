# Dynamic limits (owner directive 2026-10-19), architect plan

Requirement: "loss per trade, daily loss, profits ... dynamic according to strategy ... always dynamically calculated".
Sources: DESIGN.md "Sizing" (docs/DESIGN.md:112: 0.5% x multiplier 0.25-1.5, 1.5xATR stop) and "Hard limits"
(:114-124: 1% per trade, 2%/£50 daily). TRACEABILITY R59 (ADR 0031 adaptive limits, implemented), R70/A19 (ADR 0037
probe). New row R-next, status planned. Needs a NEW ADR (0031's rules are frozen; "a change to any rule is a new
ADR"): reserve with `scripts/docs_assemble.py new-adr --slug dynamic-limits`.

## Design: extend ADR 0031, no parallel path
Nightly `limits_job` (ops/limits_job.py:318 `decide_account`) already builds per-strategy demo/shadow evidence and
the decision the engine reads (engine/limits_apply.py:79). Add ONE new decided field, per-strategy risk per trade.

1. Per-trade risk r_s (adaptive.py, new `strategy_risk(trades, base) -> RiskTerm`), point-in-time: demo fills and
   shadow taken trades with exit_ns < decision-day start (existing `demo_evidence`/`shadow_evidence`), strategy = model
   version (existing key). f_s = the existing A1 per-trade/per-day quarter-Kelly fraction (adaptive.py:311 math,
   before /Q_SCALE). Shrinkage w = n_days/(n_days+20) (cluster count = trading days). r_s = base + w(f_s - base),
   quantised to 0.1% steps, clamped [0.25%, ceiling]. base = today's 0.5%. Loosen only with demo n >= 20 and the 0031
   3-day/cooldown rules; shadow may only tighten; LCB <= 0 gives 0.25%.
2. Intraday tighten-only factors (engine, at each decision): k_vol = 0.5 when 20-day median ATR15 >= 1-year p90
   (limits_job.py:179 `atr15_stats`); k_dd = 1 -> 0.5 linearly as drawdown from HWM goes 2% -> 4%; k_day = 0.5 once
   today's loss >= 50% of the allowance. r = r_s * k_vol * k_dd * k_day; the multiplier and the 1% hard cap still
   apply at sizing.py:74-84.
3. Daily allowance: the existing 0031 ladder plus one new tighten rule, vol regime >= p90 -> one step down. £50 abs
   unchanged.
4. Probe: per probe, max_loss = min(loss of 0.01 lot at the challenger's own label-spec stop x ATR (cost_probe.py:395
   `stop_loss`), budget_share x daily limit / max_open). max_open_dyn = clamp(floor(budget_share x limit / median
   probe stop loss), 1, cfg.max_open). Config `max_loss`/`max_open` become the ceilings and the fallback.
   budget_share stays static (a priority share of an allowance that is already dynamic).
5. Targets: NOT dynamic. Reading taken: p is calibrated to the label-spec barrier, and the gate's EV check
   (gate.py:642) uses target_atr, so changing the target from MFE would invalidate both. An MFE-based exit is a
   pre-registered research trial instead.

Ceilings: demo r <= 1% (RiskSettings le=0.01, config.py:148); live/tiny-live bounds = defaults, so tighten only
(adaptive.py:138 `LimitBounds.build`). Fail closed: missing or corrupt evidence/ATR/state gives today's fixed values
(0.5%, the config's £4/max_open) plus a dq_event.

## Changes (file:line)
- risk/adaptive.py:102 `Limits` + :239 `AdaptiveState`: `strategy_risk: dict[str, float] = {}`; :367 `step`: the
  new rules. risk/limits_profile.py:87 `RiskLimitsCurrent.strategy_risk`, `risk_terms` (the why); :175 `clamp`.
- ops/limits_job.py:318 ATR regime into the evidence.
- risk/gate.py:45 `RiskLimits.strategy_risk`, `risk_for(version) -> float`; :167 `Intent.model_version: str | None`;
  :664 pass it. risk/sizing.py:74 `effective_risk(..., risk_frac: float | None = None)`.
- engine/limits_apply.py:199 `apply` (k factors), :285 `published_limits` (+strategy_risk, why);
  engine/runner.py:1119 Intent gets model_version.
- engine/cost_probe.py:359, :428, :524; ops/accounts.py:67, :161, :376 + `Account.dynamic_limits: bool = False`;
  config/accounts.yaml (icm-demo, icm-probe true).
- Must NOT change: the RiskGate check order, exits, RiskCeilings/DEMO_ENVELOPE, daily_loss_limit_abs, the
  approvals path.

Journal: `limits_change` gets `risk_terms{version:{r, f, n_days, lcb, w, k_vol, k_dd, k_day, reason}}`; the entry
audit gets `risk_frac`, `risk_source`; probe decisions get `probe_max_loss`, `probe_max_open`, `basis`. The API
schema regen gives the dashboard "why this limit today".

## Non-negotiables
Sizing stays inside RiskGate.check. Exits untouched. Features, asof_join, phase gate: untouched. Times via epoch_ns.
Demo first: the flag defaults off and live is tighten-only. No identifiers in docs or config.

## Tests first
test_adaptive_limits.py::test_strategy_risk_shrinks_to_base_with_few_days,
::test_negative_lcb_gives_quarter_percent, ::test_strategy_risk_never_above_one_percent,
::test_shadow_only_tightens_strategy_risk, ::test_live_strategy_risk_never_above_default,
::test_vol_regime_at_p90_steps_daily_down (>= boundary), ::test_corrupt_evidence_gives_fixed_values;
test_risk_gate_split.py::test_gate_sizes_with_strategy_risk, ::test_unknown_version_uses_base;
test_adaptive_limits_wiring.py::test_k_dd_at_2_and_4_percent, ::test_k_day_at_half_allowance,
::test_restart_keeps_strategy_risk, ::test_flag_off_is_todays_behaviour;
test_cost_probe.py::test_probe_max_loss_from_native_stop, ::test_probe_max_open_never_above_config,
::test_probe_loss_unknown_refuses.

## Lanes (disjoint files)
- A (controller): adaptive.py, limits_profile.py, limits_job.py, tests/test_adaptive_limits.py, the ADR.
  Defines the `strategy_risk`/`risk_terms` contract first.
- B (gate+engine): gate.py, sizing.py, limits_apply.py, runner.py, api schema + web types, test_risk_gate_split.py,
  test_adaptive_limits_wiring.py.
- C (probe+config): cost_probe.py, ops/accounts.py, config/accounts.yaml, test_cost_probe.py.

Reviews: trading-safety (B, C), quant (A formula, pre-registration), CFO (demo 1% per-trade appetite), code,
security. Docs: ADR, TRACEABILITY row, HANDOFF fragment, RUNBOOK (rollback: `dynamic_limits: false` and restart).
Migration: the new fields are additive JSON in adaptive_state. The first decision is the 23:05 job after the deploy;
until then the fixed values apply. Owner-only: dynamic limits on live, any ceiling change.
