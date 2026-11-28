# Pre-registration amendment (post-run): D-12-xt binds ρ_u,inst, ρ_u,day and τ_a per tf (`sr-v3` rev 10 -> rev 11)

Amends `docs/research/prereg-2026-11-26-sr-v3.md` (H-103) rev 10: section 2.3 (the measured ladder inputs), 3.6
(`LAMBDA_INST`) and 9a (work item D-12-xt). Quant ruling 1 on PR #525 (2026-11-28), recorded by the data engineer.

**Named post-run.** The binding rule below was chosen AFTER the D-12-xt re-run
(`docs/research/rho-measurement-2026-11-28-xt-v1.json`) was read. The D-12-xt work item had set the bucket rule
max(D-12 recorded, re-run) per class × hold bucket. Under hold = H, the gt4h bucket now spans holds from 8 h to 20 d, and
its worst cell is set by the 1d and 1w holds.

**Why choosing the rule after the run is acceptable** (quant ruling 1 on #525):
- ρ_u,day, ρ_u,inst and τ_a are nuisance second moments: they scale n_eff and the ridge prior. None is an effect
  estimate, and none enters a test statistic as a mean.
- They are measured on the control grid and on three replayed families that are neither sr-v3 nor H-102 (`box_break_close`,
  `sweep_absorb`, `session_range_break`), on the learn window 2010-17 only.
- No trial outcome was seen. No sr-v3 or H-102 label value and no dense-label store value was read (the labels
  were computed in the script). No confirm, test_old or holdout file was opened.
- The D-12 recorded binding stays the floor for every tf's bucket, so no ρ falls below its D-12 value and nothing loosens
  against what the card already used.

**The rule** (`binding_by_tf` in the file; `PER_TF_RULE`):
- ρ_u,inst(tf) = max(the D-12 binding of the tf's bucket, the cumulative maximum of the re-run per-tf value over holds
  ≤ this tf). The same rule applies to the D-13 vs-gold level (`rho_u_inst_vs_gold_day_mean`).
- ρ_u,day(tf) = max(the D-12 bucket value, the re-run at that tf). It is undefined for H ≥ 1 d: one candidate per
  instrument-day, so the design effect is 1. There the card's prior floor stands (`sr_v3.rho_inputs`).
- τ_a(tf) = the re-run at that tf only (the R units must match). λ_a = 1.35 / τ_a².
- `rho_day_le_60m` stays at 0.25 (quant ruling 2). No per-class le-60m value is set.

**Readers.** `sr_v3.rho_inputs` and `data_sufficiency.measured_rho` read `binding_by_tf`. A gate read recomputes the
per-tf ρ_u,inst from the cells file and the `binds_with` D-12 file, both checked by sha256
(`data_sufficiency.verify_inst_by_tf`). `config/data/sufficiency.yaml` pins the file's sha256.
`PowerFirstSettings.rho_day_for(hold, class)` returns max(prior, the class's binding ρ_u,day at that tf) from the
pinned file (`research.power_first.rho_xt_measurement`). Tests: `tests/research/test_measure_rho_xt.py`.

**The per-tf binding** (upper 95 %, week-block bootstrap B = 2,000; `n/a` = H ≥ 1 d, design effect 1):

| quantity · class | 1m | 5m | 15m | 30m | 1h | 2h | 4h | 1d | 1w |
|---|---|---|---|---|---|---|---|---|---|
| ρ_u,inst · metals | 0.885 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| ρ_u,inst · fx10 | 0.443 | 0.297 | 0.338 | 0.338 | 0.338 | 0.338 | 0.338 | 0.427 | 0.554 |
| ρ_u,inst · pooled | 0.397 | 0.175 | 0.198 | 0.203 | 0.211 | 0.269 | 0.269 | 0.380 | 0.443 |
| ρ_u,inst · gold+fx10 | 0.399 | 0.262 | 0.317 | 0.317 | 0.317 | 0.317 | 0.317 | 0.410 | 0.510 |
| ρ_u,inst vs gold · metals | 0.885 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| ρ_u,inst vs gold · pooled | 0.215 | 0.255 | 0.325 | 0.325 | 0.325 | 0.337 | 0.337 | 0.655 | 0.763 |
| ρ_u,inst vs gold · gold+fx10 | 0.256 | 0.304 | 0.350 | 0.350 | 0.350 | 0.350 | 0.350 | 0.665 | 0.750 |
| ρ_u,day · gold | 0.224 | 0.215 | 0.447 | 0.273 | n/a | n/a | n/a | n/a | n/a |
| ρ_u,day · metals | 0.188 | 0.202 | 0.299 | 0.260 | n/a | n/a | n/a | n/a | n/a |
| ρ_u,day · fx10 | 0.062 | 0.073 | 0.136 | 0.118 | n/a | n/a | n/a | n/a | n/a |
| ρ_u,day · pooled | 0.054 | 0.056 | 0.127 | 0.112 | n/a | n/a | n/a | n/a | n/a |
| ρ_u,day · gold+fx10 | 0.056 | 0.062 | 0.126 | 0.108 | n/a | n/a | n/a | n/a | n/a |
| τ_a · metals | 0.234 | 0.216 | 0.265 | 0.264 | 0.312 | 0.251 | 0.571 | 0.578 | 0.350 |
| τ_a · fx10 | 0.069 | 0.079 | 0.078 | 0.084 | 0.100 | 0.102 | 0.172 | 0.210 | 0.198 |
| τ_a · pooled | 0.092 | 0.087 | 0.099 | 0.100 | 0.123 | 0.117 | 0.243 | 0.258 | 0.207 |
| τ_a · gold+fx10 | 0.071 | 0.080 | 0.083 | 0.087 | 0.098 | 0.103 | 0.164 | 0.207 | 0.196 |

The figures the ruling expected all hold:
- gold+fx10 ρ_u,inst: 0.317 at 15m-4h (the D-12 floor), 0.410 at 1d, 0.510 at 1w.
- pooled ρ_u,inst: 0.198 / 0.203 / 0.211 at 15m / 30m / 1h, 0.269 at 2h-4h, 0.380 at 1d, 0.443 at 1w.

**Consequences.**
- Gold alone at 15m (H = 8 h): the binding ρ_u,day is 0.447, against the 0.25 prior. The gold-alone n_eff figures
  that the H-105 and H-106 cards computed at ρ_day 0.25 overstate 15m n_eff by about 20 %: with m ≈ 2.9 labels a day,
  (1 + 1.9 × 0.25) / (1 + 1.9 × 0.447) ≈ 0.80.
- The pooled gold_fx body keeps its D-12 divisor at 15m-4h. Its divisors at 1d and 1w rise.
- D-13 admission (a new instrument vs gold, ≤ 0.2 per hold bucket) needs its own per-instrument, per-tf run at xt-v1.
  This file measures no candidate instrument, only the class memberships.
- K effect 0. No sr-v3 label or Stage 0 statistic exists yet, so nothing read is voided.
