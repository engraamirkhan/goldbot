- 2026-11-15 (branch feat/replay-only-controls; R157): S0 is now a replay-only control, S0R gets the fills its
  stop-exit read needs, and a replay-parity monitor watches the r2 replay. Performance-analyst spec 2026-11-15;
  amendments docs/research/prereg-amendment-2026-11-15-{replay-only-controls,eval2-s5-calibration,
  hv-rule3-replay-control}.md.
  - `control_mode: replay_only` on a spec row (`research.arms.Arm`, not hashed, role control and gold only). The
    engine refuses such an arm first thing in `exploration._send`, so it never reaches the budget, the RiskGate, an
    order intent or the order core. Its candidates are still recorded (blocked `replay_only`, mu 0) and labelled by
    the r2 replay. S0 is the one replay-only row. It is not retired, because a retired arm records no candidates.
  - S0R (`research.arms.STOP_EXIT_READS`) is exempt from control thinning (propensity 1). It stops itself at 100 stop
    exits (`refresh_cost_reads`, `exploration_arm_cost_read_complete` with `stop_exits`). The thinning had starved it at
    86 stop exits.
  - S1A and S1B carry `retired_day: 2026-11-13`, as the 2026-11-06 amendment said. Neither has entered since
    2026-10-30. GS1C, IEX1H, VEX15 and VEX1H stay broker-traded.
  - New step in the daily review: `research/replay_parity.py`. It reads closed gold fills against their r2 labels per
    broker x TF band and pooled. It FIRES when the day-clustered 95 % CI of the mean gap lies outside +-0.02 R, or
    when barrier agreement is under 90 % in two consecutive 300-fill windows. A fire does three things: it adds a
    `parity` incident (posted to the learning-ops issue), it fails the feedback-loop check for 3 days, and it writes an
    automatic `review_actions` row (kind `replay_parity`). That row is the revocation: `eval2.forward.apply_s5(...,
    revoked=)` and `catalogue.costs.deciding_cost(..., s5_revoked=)` treat the scope as not validated. Nothing lifts a
    revocation except a dated amendment.
  - Not done: the strategy ranking's matched read does not use S0's replay labels or the revocations yet (HV Rule 3
    amendment, implementation status). S0 keeps its `control_share` 0.25 of the scalp pot, so no other arm's room
    widens. Review: trading-safety-reviewer (order path `_send`, S0R exemption) and quant-reviewer (parity rule,
    amendments).
