- 2026-11-20 (branch feat/gd1-asia-learning-arm; R174): GD-1 A1 (H-98, trial #3440) runs forward on demo as a
  learning-class arm: long gold on the 1h close at 00:00 UTC every weekday, exit 07:00 UTC, 0.01 lot.
  - GD1D on icm-demo and GD1P on icm-probe (one arm id per host), rule `clock_hold`
    (`goldbot/research/arm_rules_clock_hold.py`, rule-code version 1). No signal and no target (50 R). The stop is a
    fixed $48 catastrophic stop at the broker, the widest that fits the ADR 0043 swing cap of £40 at 0.01 lot.
    3 x daily ATR (about £150-190) would be refused by every per-trade cap.
  - The order path is the exploration fence. As a signal the RiskGate refuses a time exit
    (`time_exit_ev_unsupported`); the RiskGate itself is unchanged.
  - The engine closes the position on the wall clock at its signal close plus its hold (`exploration._clock_due`),
    so a missing bar never holds it past 07:00. Exits are never gated.
  - Each arm has a replay-only control, GD1CD and GD1CP (`clock_hold_control`): long for the same 7 hours at one
    seeded hour from 07:00 to 13:00 UTC a weekday (`control_mode: replay_only`, never an order). It matches through
    `control_match_refusal` (same timeframe, stop, target and hold). It is the deploy plan's "complement" control
    cut into 7-hour pieces.
  - Kill rule per arm (`config/arm_stops.yaml` GD1D/GD1P, applied by the daily review):
    - cost: realised round trip > 1.1 bp after 40 fills;
    - loss: mean net <= -4 bp at 120 fills;
    - continue: at 250 fills, only if mean net >= +3.5 bp.
    These sit beside H-61's swing stop, the £800 learning loss cap, the 300-fill read and the end. To support it,
    `arm_stops.Safety` became a list of one-off checks, with `mean_bp_lt`, `cost_bp_gt` and a new
    `realised_cost_bp`. WFRI's check is unchanged.
  - Review round (same day): no kill check falls due in the first 45-day run (about 23 fills an arm after the HAR
    abstention), so only H-61 and the £800 cap can stop it then; a renewal changes `since`/`until` only and adds no K.
    The arm-versus-control read is replay labels against replay labels, never fills against the control. A single
    `safety` mapping (WFRI) keeps its old all-readable rule; only the list form waits for readable figures.
  - Runs 2026-11-23 to 2027-01-04. K +4 (2 arms + 2 controls): run `scripts/record_learning_k_rows.py` on the brain
    before the first fill. The arms stay refused until those rows exist. icm-probe gains a one-slot swing pot.
  - Pre-registration: docs/research/prereg-amendment-2026-11-20-gd1-asia-learning.md. The GD-1 card and result are
    cherry-picked from research/gold-daily-1120.
  - Reviews: trading-safety-reviewer (the entry at 00:00, the wall-clock exit, the $48 stop at the £40 cap,
    icm-probe's swing slot) and quant-reviewer (the control, the kill rule, the cost definition, K).
