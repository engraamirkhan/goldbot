- KB specialists, quant review fixes before any dispatch (`docs/research/prereg-kb-2026-10-18.md` §7a): `sweep_absorb`
  and `eq_liquidity_run` label on 1m barriers (`Specialist.barriers_on_1m`, `labels.triple_barrier_1m`: entry at the
  close, the stop first only inside a minute; refused without 1m bars). `eq_liquidity_run` refuses to dispatch
  (`research_pass.py`, before any bar is read) unless `docs/research/kb5-stage1.json` is committed and its recomputed
  stage-1 reading passes. `scripts/kb_prereg_counts.py` checks the trial's candidates against 3,349 / 2,079 within a
  pre-registered ±3% (exit 1 = stop); run it before each family's first dispatch. Stage-1 bootstrap resamples the
  control too; `triple_barrier_delayed` honours a per-candidate `target_atr`. Model-filtered metrics of these
  dispatches are not evidence.
