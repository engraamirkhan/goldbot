# Pre-registration H-80: adaptive exits on the box break-and-close, one family on 15m/30m/1h/4h (2026-10-26)

Trade management researcher. Written after the 2010-2017 discovery read in `exits-adaptive-targets-2026-10-26.md`
section 4 and **before any 2018+ outcome, any BX arm fill or any holdout bar** (2025-10-01..2026-09-30) is read. Git
history is the proof. Nothing is dispatched, funded, registered or configured by this file; the research director
decides. H-80 is chosen above the highest id in flight (H-79, `research/time-structure-integration`); renumber on merge
if taken.

## 1. Configuration (frozen)
- **Entry:** `box_break_close.decide_with_stop` with every module constant unchanged (BREAK_DEPTH 0.2, MFI(12) 30/70
  turning in t-4..t, swing trend not against, not in the golden zone, structural stop within 1.5 ATR as a filter), on
  15m, 30m, 1h and 4h mid bars, decision at the bar close, market entry after it. Arms' common filters
  (`arms-2026-10-25.md` section 0). One position per arm.
- **Stop:** 1.5 x ATR14 of the decision timeframe at the signal bar (= 1 R), frozen.
- **Exits:** RUNNER (BXR, real orders): break-even at +1 R (stop to entry + side x $0.23), chandelier 2.5 ATR_tf behind
  the best exit-side price armed at +1 R, effective from the next bar close; no target; time exit 96/72/48/24 bars;
  Friday rule. STRUCT (BXT, virtual): target the first box in force at t lying at or beyond 2 R, else 2 R. FIXED (BXF,
  virtual): 2 R. BXT/BXF are computed on each BXR fill's 1m path from the real fill price.
- **Size:** 0.01 lot always; no partials (the small-account form).

## 2. Economic reason
Breaks that continue are the only source a runner can harvest (Kaminski & Lo 2014, *Journal of Financial Markets* 18,
234-254, *(not re-checked)*: trailing exits pay only with serial correlation). Boxes are not magnets (R-13; slice G key
levels: no magnet), so a structural target is expected to do no better than 2 R. Grade C-D.

## 3. `research.yml` inputs
None for the arms (exploration, 0 registry trials). R2 (below), if funded: `family: box_break_close`, timeframes
`[15m, 30m, 1h, 4h]` pooled with a `timeframe` stratum, years 2018-01-01..2025-09-30, exits E0/E4/E8 as defined in
`docs/research/candidates/time_structure/exits_adaptive_discovery.py` (no other exit), costs: research $1.07/oz flat and measured $0.23 +
overshoot (S0R's measured value when it has 30 stop exits, else $0.70) on the stopped share.

## 4. P4 screen expectation (from the 2010-2017 discovery half)
Events: about 5,000 pooled one-at-a-time trades in 2018-2025-09 (rate 2.6 a trading day pooled; 4h about 1 a week).
Gross: E0 about 0, E4 about +0.04 R, E8 about -0.016 R. Net at measured cost: all negative (E0 -0.045, E4 -0.021 R at
today's ATR). P4 for a family promotion is not expected to pass; the read is about the exit.

## 5. Reading rules (written before any data they read)
- **R1, forward arms (icm-probe; host icm-demo by amendment 2026-10-26: a 1.5 ATR stop of about £9 exceeds icm-probe's £6.50 cap, so BXR is registered on icm-demo; the read is unchanged. Quant C3, before any fill).** Kill: the H-61 per-arm cumulative-R stop and ADR 0043 caps, unchanged. At 300
  pooled BXR fills and every 300 after: report paired BXR - BXF and BXT - BXF (net, day-clustered t) per timeframe and
  pooled, labelled "underpowered: detectable 0.13 R / 0.08 R at 300". **No adoption from R1 alone** before 3,000 pooled
  fills. Measured trail-stop slippage is reported and replaces the $0.70 prior in R2 once >= 30 trail exits exist.
- **R2, confirmation read (2018-01..2025-09, evaluator runs it once).** Two pre-specified paired comparisons, Holm over
  the two, one-sided:
  - Adopt RUNNER as the box family's exit, and as the default candidate exit for the next breakout pre-registration,
    only if all hold: pooled E4 - E0 gross > 0 with day-clustered t >= 2.0 (Holm-adjusted); same sign in at least 3 of
    the 4 timeframes (a timeframe with < 150 trades is "no read" and the rule becomes 2 of 3); pooled E4 net >= E0 net
    at the measured cost. Otherwise RUNNER is not adopted and the BXR arms stop at their next kill check.
  - Close STRUCT (next-box targets) for the box family if pooled E8 - E0 <= 0; adopt only if > 0 with t >= 2.0 and
    3 of 4 timeframes.
  - No best-timeframe pick: a timeframe's own result never selects it.
  - Family: R-15 stands. Only if pooled E4 net > 0 at the measured cost with t >= 2.0 may a registry trial be written
    for the box family (R-15's "measured costs below half the prior" condition is met: $0.23 vs $1.07), charged as a
    retired-family exploration trial at the registry K of that date.
- Reported, not read: R-multiple deciles, MFE/MAE, exit mix, the best-day capture ratio (D-4 of
  `prereg-exits-2027Q1.md`), Friday-rule exits.

## 6. Gates and trial cost
- Arms: 0 registry trials. K_eff +1 on this row (E4 chosen among the runner forms after the discovery read).
- R2: **1 registry trial recommended** (it spends the 2018-2025-09 half on these exits), or a 0-trial diagnostic with
  K_eff +2 if the director rules so. Gate-1 DSR is not the adoption bar for an exit (a paired, pre-specified
  comparison); any family promotion stays under gate 1 at the registry K.
- Prior: P(RUNNER adopted) about 35%; P(STRUCT adopted) about 2%; P(box family trial written) about 3%.

## 7. Known-failed ideas, not re-tested
Trails 1.5 ATR after 1.25 ATR and half-at-1 ATR on R-03/R-04; exits as rescue of a primary with gross t < 2;
break-even as an edge; partials at 0.01 lot (impossible); structural box-far-side stops (S1-S3: gross -0.16 to -0.25 R);
nearest-box targets (inside 2 R 97-98% of the time).

## 8. R2 verdict (evaluator, 2026-10-30, single read, 2018-01-01..2025-09-30)
Run once by the section 5 rule with `candidates/time_structure/exits_adaptive_confirmation.py` (the discovery script's
entries, E0/E4/E8 walks, Friday rule and costs unchanged; bars from 2025-10-01 never loaded). Registry row written
before the first score (pre-registration, then the evaluated row, then the verdict; the registry file is local-only,
local trial no. 1, to be synced into the brain registry as the 2026Q4 trial). Costs: 0.23 USD/oz plus a 0.70 USD/oz
overshoot prior on the stopped share (S0R has no 30-stop read); flat 1.07 USD/oz also reported.

Pooled paired comparisons (same entries, day-clustered t, one-sided):
- E4 (RUNNER) minus E0: n 5,355, gross -0.0177 R, t -1.12, p 0.869 (Holm 0.869), net at measured cost -0.0889 R.
- E8 (STRUCT) minus E0: n 5,355, gross -0.0002 R, t -0.02, p 0.508 (Holm 1.0), net -0.0076 R.

By timeframe, E4 minus E0 gross: 15m -0.030, 30m -0.044, 1h 0.057, 4h 0.047 (trades 2855, 1478, 805, 217).
Day-clustered t: -1.47, -1.54, 1.51, 0.82. All four have at least 150 trades, so 3 of 4 is needed; 2 of 4 are positive.
Pooled levels, gross / net at measured cost: E0 +0.0115 / -0.199, E4 -0.0062 / -0.288, E8 +0.0113 / -0.206 R.

**Verdict: FAIL (stop).** RUNNER is not adopted: pooled E4 minus E0 gross is negative (needs positive with t at least
2.0), 2 of 4 timeframes agree (needs 3), and E4 net is below E0 net. The BXR arms stop at their next kill check.
STRUCT is closed for the box family (pooled E8 minus E0 is -0.0002 R, not above 0). No family registry trial is
written (pooled E4 net -0.288 R). The verdict is the same under any reading of "t 2.0 (Holm-adjusted)": the raw t is
-1.12. The discovery-half +0.039 R (t 2.4) did not replicate. Caveats: the timeframes are not independent samples;
trades open at the window end are time-exited at the last bar before 2025-10-01; the 0.70 overshoot is a prior and
cannot change the sign of the gross failure.
