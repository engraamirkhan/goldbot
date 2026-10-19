- Shadow roster amendment 2026-10-19 (docs/research/shadow-roster-2026-10-19.md): the live state path for the research
  rule families. The shadow book now trades a candidate on its own barriers (`engine/shadow.py` `candidate_barriers`:
  `risk_usd` as R, `target_atr` in R, `max_bars`, cost threshold in the same R) and reads a `barriers_on_1m` family's
  barriers on the decision bar's 1m bars (`on_bar(minutes=)`, as `triple_barrier_1m`; tests reproduce the research
  labels exactly). An agent whose R is candidate-borne or read on 1m bars is `shadow_only` (the live path sizes with
  label_spec x ATR(14)). `sweep_absorb` (H-48, 15m, `{"rung": "prereg"}`) joins the roster (4 of 40) as a frozen rule
  agent with a RuleModel. Still blocked: the box-course families (context bars and >300 4h bars beyond the engine's
  tail, rung choice), `eq_liquidity_run` (stage-1 result not committed), price_action groups (no state path).
