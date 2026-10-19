- KB hypotheses implemented, not yet run (`docs/research/prereg-kb-2026-10-18.md`): specialists `sweep_absorb` (H-48) and
  `eq_liquidity_run` (H-49 stage 2), one typed preset `prereg` each, `screening` (out of GRID_V1, the founders and the
  pools), plus `scripts/kb5_magnet_base_rate.py` (H-49 stage 1, counts and base rates only) and
  `scripts/kb1_retest_selection.py` (D-KB1). Labels gained an optional per-candidate `target_atr` column (H-49's target is
  a price level). The prereg text's "reward:risk 1.1-2.3" for H-49 is an arithmetic slip: the fixed distances give
  0.73-1.67 R (flagged in the specialist docstring). Counts on the pinned series are owed (no market data in the
  sandbox). Dispatch order: H-49 stage 1 -> H-48 -> (only if stage 1 passes) H-49 stage 2. The 2026Q4 override
  (53 -> 55) is NOT in this commit (surfaced to the owner).
