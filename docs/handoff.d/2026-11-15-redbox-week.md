- 2026-11-15 (branch feat/kog-redbox-arm; R157): the red-box week, the owner's directive of 2026-11-15: "trade every
  red box break and close to the next red box on 15m, 1h, 4h" on demo for one week, then review it. The red boxes
  are our rebuilt box indicator (the replica's `sr_channels`) on each arm's own timeframe. They are not KnightsofGold's
  post levels: the owner corrected this the same day, and nothing reads `tv_ideas.db`.
  - Three learning-class arms RB15/RB1H/RB4H on icm-probe at 0.01 lot, one open each
    (`goldbot/research/arm_rules_redbox.py`, rule-code version 1). The rule: a close through a box top is a long,
    through a bottom a short. The stop is the broken box's far side plus 0.1 ATR. The target is the next box edge
    beyond the close. The hold is the roster's. There is no filter. Every order goes through `_send` and the RiskGate,
    and every exit is automatic.
  - Each arm has a replay-only control, RBC15/RBC1H/RBC4H: the same rule on seeded random ladders that keep the
    boxes' widths and gaps. The controls send no order. Every control candidate is recorded skipped `replay_only` and
    labelled by replay. A control matches its arm through the new per-trade bracket rule
    (`arms.control_match_refusal` -> `_per_trade_refusal`).
  - Every candidate journals `exploration_redbox_features` (`goldbot/engine/redbox_week.py`): money flow and the
    roster's money-flow confirmation, swing trend, golden zone, break depth %, box height, close inside another box,
    session, and the 1h/4h box holding the close. These are review features only. The hook in
    `exploration._signal` is three lines.
  - Stops: £100 loss cap per arm (£300 for the family over the week), the £220 day through the RiskGate, and no
    candidate from 2026-11-22 22:00 UTC (`params.end_utc`, with `learning.until` as the backstop).
  - K +6 (3 arms + 3 dedicated controls), written on the brain by `scripts/record_learning_k_rows.py` before the
    first fill. The arms stay refused until those rows exist.
  - Pre-registration: docs/research/prereg-amendment-2026-11-15-redbox-week.md (rules, control, caps, the review's
    questions).
  - Still applied, unchanged: `window_0207` (02:00-07:00 UTC) and the N-3 HAR low-range-day abstention. Both shadow
    the refused signals. Review: trading-safety-reviewer (new arms on the order path, the per-trade bracket match, the
    `_signal` hook) and quant-reviewer (rule, control, features, K).
