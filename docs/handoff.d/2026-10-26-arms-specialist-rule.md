- Arm rule `specialist` (2026-10-26, arms expansion wave 1, lane L1; docs/research/arms-expansion-2026-10-26.md
  section 3). An exploration arm can now run any registered specialist family: `rule: specialist`, the decision
  `timeframe`, and `params.specialist` (family name), optional `params.preset` and `params.config` (config wins over
  the preset), plus the common window and stop-band params. goldbot/research/arm_rules_specialist.py calls the family's
  own `candidates_in_context` on research's context (`context_tfs`) and takes the stop (`label_spec.stop_atr` x its
  `barrier_atr`, else ATR(14)), the target and the time exit from its `label_spec`, a candidate's own `target_atr` /
  `max_bars` kept. Barrier form only: `exit_spec` (partial/trail) is not run at 0.01 lot, a next-open family fills at
  the close, and `complete_windows` (asia_drift) is not applied. An unknown family, `tv_event` / `requires_tv`, a
  delayed signal, an unknown preset or a refused timeframe/config fails at arm load and at run.
  `RuleContext.spec_ctx` carries point-in-time tables (e.g. the smc replica state) for families that need them; the
  engine does not pass it yet. Tests: tests/test_arm_rules_specialist.py (parity with `candidates_in_context` and
  with `research.pipeline.prepare`'s stop and target for box_break and trend, prefix invariance, refusals, hash).
