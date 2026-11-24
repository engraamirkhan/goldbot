- 2026-11-24 (branch feat/replay-only-retired-arms; R176): retired no-evidence gold arms record again as replay-only.
  Owner 2026-11-24, "more zero-risk learning data"; docs/research/prereg-amendment-2026-11-24-replay-only-retired-arms.md.
  - `control_mode: replay_only` is now allowed on a gold arm of any role. On a retired row the retirement stands (no
    entry anywhere). The rule runs again only to record: taken 0, blocked `replay_only`, labelled by the r2 replay.
  - Engine: a replay-only candidate is recorded at its signal (`exploration._signal` -> `_replay`) and never queued.
    The `_enter` sweep and `_send`'s first check stay as guards. `Exploration.recording` holds the arms whose rule
    runs although their entries are refused. Excluded: hash-refused, invalid, dark or 1d/1w arms, and a dedicated control without its own retirement, which falls silent with its learning arm (quant C2). They are never dormant or
    de-registered. `REPLAY_RETIRED_DAILY_CAP` (1,000 a UTC day per book, on the engine clock) bounds the records, skipped ones included, with the counter seeded from today's rows at the start; the first record over
    it is journalled `exploration_replay_cap`.
  - Switched to replay-only: S1A, S1B, F1A, F1C, G1A, G1C, Z1P, Z2P, Z5P, LCZ1P, LCZ2P, LCZ5P, Z3D, Z9D, Z10D, LCZ3D,
    LCZ9D, LCZ10D, ZCD, IEX15. New rows: RBR15/RBR1H/RBR4H and RBRC15/RBRC1H/RBRC4H on icm-probe. These carry the
    red-box rule body with `end_utc` 2026-12-31 22:00 UTC; the old rows stop at their hashed end. They are born retired.
  - Left retired: GS1/GS1C (the GS1 verdict due 11-27 reads up to 11-22), the old RB*/RBC* rows and every non-gold row.
  - No K: no registry row, no learning block, no qualification.
  - Expected volume: about 280 more candidates a day, about 440 in all.
  - Rollback: drop the `control_mode` edits and the six RBR*/RBRC* rows and registrations, then restart.
