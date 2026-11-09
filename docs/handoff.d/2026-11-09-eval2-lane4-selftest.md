- **EVAL-2 Lane 4, self-tests (R110).** `scripts/eval2_selftest.py` runs S0-S6 against the frozen API (prereg
  section 4.10), writes a JSON report to `~/tmp/eval2_selftest/` and exits 1 on any FAIL or BLOCKED. Real bars are
  explore 2010-01-01..2017-12-31 only (no 2009 warm-up, nothing from 2018 on; the bar cache is re-checked on load).
  **S0 on real bars, registered NULL-RAND cells (52 cells, n 53,264, 20 twins): pooled gross +0.0037 R (t 0.64), excess
  +0.0025 R (t 0.38), chi2 dispersion p 0.54; the buy-every-close and drift-walk sentinels fail E9. Literal cell floor
  FAILS in DE40 4h (-0.071 R, t -1.19) and XAGUSD 4h (-0.066 R, t -1.34).** Neither cell is significant. A sound
  harness is expected to fail that floor in 2.0 of the 19 cells at n >= 300, because the floor has no sampling term.
  A quant amendment is requested; until it is signed, S0 reads FAIL. M_eff (mean twins per signal): 5m 18.4, 15m 20.0, 30m 19.5,
  1h 13.0, 4h 4.3, 1d 13.8, 1w 4.1. S3 harness half: a +0.20 R plant was detected in 98 of 100 runs and a 0 R plant
  in 1 of 100; the pooled planted excess was +0.193 R (SE 0.005). S4 lint: 4 of 4 realised filters refused. S6 runs
  with every mismatch at 0. S1, S2, the S4 probe and S5 read BLOCKED until Lanes 3 and 5A land.
- **Holdout seal (`tests/test_holdout_seal.py`).** A stale or foreign `$CATALOGUE_CACHE/<win>/*.npz` used to be
  served as is, including holdout bars and a 2018 bar in the explore cache (an unsealed confirm read).
  `dispatcher.checked_cache` now fails closed on load. A static check makes every `goldbot/research` module that opens
  bar files name a holdout guard.
- **Lane 1 follow-ups.** `sim.session_day` now uses `costs.rollover_table()`. The full S0 random-walk test
  (`test_catalogue_harness_sane`) now carries `@pytest.mark.selftest` instead of the `GOLDBOT_SELFTEST` skipif.
  `selftest.yml` paths now also cover the script, the cost table and the self-test files.
