- 2026-11-24 (branch feat/demo-learning-cohort; R177, ADR 0095): the self-retrain's ten retired arms send real demo
  orders again as a capped learning cohort on icm-probe. Owner 2026-11-24, "it is quite concerning that it is not
  trading at all"; card docs/research/prereg-amendment-2026-11-24-demo-learning-cohort.md.
  - New `exploration.cohort` block (accounts.yaml, `ops.accounts.LearningCohort`). It is refused off a demo
    probe_only account, for an arm that is a control, not at the minimum lot or above the per-arm cap in one trade,
    and for an arm registered on a second account (`cohort_clashes`).
  - Engine (`engine/exploration_cohort.py`): at start a named retired gold broker-mode arm joins and leaves
    `retired`. Its caps replace the evidence stops: daily review, cost read, cost or edge-cap suspension, learning
    block.
  - Cohort caps: one open per arm, GBP 15 net per arm since 2026-11-24 (stops it for good), GBP 50 net for the ten
    per ISO week (held to Monday 00:00 UTC), until 2027-01-15. Each check counts realised loss + open risk + the new
    trade. It fails closed until the ledger is read. Fills are stamped `evidence_class: learning_cohort`. An arm
    stopped by its cap or by the end records replay-only again.
  - Every existing cap still binds.
  - Arms: RBR15, Z3D, Z1P, Z9D, Z10D, S1A, S1B, F1A, Z2P, Z5P; their spec rows go back to `control_mode: broker`
    and keep their `retired_day`.
  - Z3D/Z9D/Z10D moved from icm-demo to icm-probe with their replay-only controls LCZ3D/LCZ9D/LCZ10D (share 0.01).
    RBR15's per-trade cap goes from GBP 30 to GBP 15.
  - Controls (LCZ*, F1C, RBRC15, S0, GD1C*) stay replay-only. GD-1 and every live account are untouched. K = 0.
  - Health: entry_silence counts the cohort as supply and shows its week-to-date P&L and hold. A silence WARN while
    the cohort is held is the cap.
  - Expected volume: about 30-37 orders a day (the 30-a-day scalp cap binds), and the weekly cap is likely to bind
    mid-week.
  - Rollback: drop `cohort`, move the three Z*D and their LCZ*D controls back to icm-demo, set RBR15 max_loss back to 30, set the ten rows back
    to replay_only, then restart icm-probe and icm-demo.
