# Sprint-200 lane `stats`: explore result (2026-11-02)

Pre-registration `stats-prereg.md` (commit `07426758`; amendment 1, the leak-free forward control, `2a11d5c0`;
amendment 2, windows W1, `c8c4be6c`). Code `scripts/sprint200/stats_screen.py`. Explore 2010-01-01..2017-12-31 only;
no 2018+ bar and no holdout row was read. ICM cost of record `cor-2026-10-31.2` + ruled overshoot + shipped swap.

**Verdict: 0 of 33 pass explore. No confirm read, no TEST-OLD read, no arm, no code under `goldbot/`.**

## Leakage tests (run before the outcome read)

- Shuffle test: every ML model trained on permuted labels, out-of-fold AUC 0.490..0.507 (band 0.47..0.53): pass.
- Lookahead test: every decision of all 33 rules recomputed on 1m data truncated at 8 random cut times (2013-2017),
  290,531 decisions compared, 0 mismatches: pass.
- Real out-of-fold AUC (sign of the forward return): 1h LR 0.536, 1h GBM 0.541, 4h LR 0.525, 4h GBM 0.515, 1d LR 0.467,
  1d GBM 0.502.

## Results (explore, net R per trade at ICM; pass = n >= 100, net > 0, t >= 2, real - forward twin >= +0.05 R)

| id | rule | explore n | gross R | net R | t | fwd twin R | real - fwd twin | real - sameday twin | yrs+ /8 | explore |
|---|---|---|---|---|---|---|---|---|---|---|
| S200-STATS-01 | HMM2-1d-LOWVOL-TREND | 78 | +0.106 | +0.012 | 0.09 | +0.008 | +0.004 | +0.015 | 3 | FAIL |
| S200-STATS-02 | HMM2-1d-HIGHVOL-REVERT | 78 | -0.235 | -0.320 | -3.29 | -0.296 | -0.024 | +0.083 | 0 | FAIL |
| S200-STATS-03 | HMM2-4h-LOWVOL-TREND | 1125 | +0.085 | +0.004 | 0.10 | -0.020 | +0.023 | +0.014 | 4 | FAIL |
| S200-STATS-04 | RVPCT-1d-LOW-DONCHIAN | 64 | +0.087 | -0.022 | -0.16 | -0.073 | +0.050 | -0.014 | 4 | FAIL |
| S200-STATS-05 | RVPCT-1d-HIGH-FADE | 51 | +0.052 | -0.036 | -0.28 | -0.059 | +0.023 | +0.162 | 3 | FAIL |
| S200-STATS-06 | RVRATIO-4h-COMPRESSION-BREAK | 74 | +0.083 | -0.034 | -0.24 | -0.100 | +0.066 | -0.004 | 3 | FAIL |
| S200-STATS-07 | KALMAN-4h-SLOPE-CROSS | 300 | +0.127 | +0.038 | 0.54 | +0.017 | +0.021 | -0.154 | 6 | FAIL |
| S200-STATS-08 | KALMAN-1h-SLOPE-CROSS | 987 | +0.014 | -0.082 | -2.04 | -0.115 | +0.034 | -0.318 | 2 | FAIL |
| S200-STATS-09 | KALMAN-1d-SLOPE-CROSS | 53 | +0.025 | -0.070 | -0.49 | -0.058 | -0.012 | -0.078 | 2 | FAIL |
| S200-STATS-10 | KALMAN-1h-INNOV-FADE | 425 | +0.070 | -0.020 | -0.33 | -0.030 | +0.009 | +0.274 | 5 | FAIL |
| S200-STATS-11 | VR-1h-TREND-MOM | 140 | +0.003 | -0.070 | -0.72 | -0.075 | +0.005 | -0.247 | 1 | FAIL |
| S200-STATS-12 | VR-1h-REVERT-FADE | 310 | -0.163 | -0.257 | -3.77 | -0.236 | -0.022 | +0.147 | 1 | FAIL |
| S200-STATS-13 | AC-1h-FOLLOW | 446 | +0.079 | -0.009 | -0.14 | -0.023 | +0.014 | -0.278 | 3 | FAIL |
| S200-STATS-14 | AC-1h-FADE | 801 | -0.070 | -0.170 | -3.78 | -0.192 | +0.022 | +0.223 | 0 | FAIL |
| S200-STATS-15 | VR-1d-MOM | 24 | -0.173 | -0.310 | -1.83 | -0.329 | +0.020 | -0.021 | 1 | FAIL |
| S200-STATS-16 | AC-4h-FOLLOW | 151 | +0.014 | -0.060 | -0.58 | -0.109 | +0.049 | -0.179 | 3 | FAIL |
| S200-STATS-17 | INTRADAY-MOM-COMEX | 2010 | +0.019 | -0.025 | -3.28 | -0.050 | +0.026 | +0.014 | 1 | FAIL |
| S200-STATS-18 | RSKEW-1d-CONTRA | 275 | +0.006 | -0.081 | -1.28 | -0.105 | +0.024 | +0.070 | 3 | FAIL |
| S200-STATS-19 | JUMP-1d-CONT | 208 | +0.050 | -0.040 | -0.59 | -0.046 | +0.006 | -0.021 | 4 | FAIL |
| S200-STATS-20 | JUMP-1d-REV | 202 | -0.004 | -0.085 | -1.20 | -0.041 | -0.044 | +0.022 | 1 | FAIL |
| S200-STATS-21 | SJ-1d-CONTRA | 269 | -0.028 | -0.114 | -1.76 | -0.135 | +0.021 | +0.069 | 2 | FAIL |
| S200-STATS-22 | MAX-1d-LOTTERY | 130 | -0.066 | -0.156 | -2.02 | -0.174 | +0.019 | +0.029 | 3 | FAIL |
| S200-STATS-23 | RSKEW-4h-CONTRA | 1141 | -0.032 | -0.114 | -3.29 | -0.104 | -0.010 | +0.131 | 0 | FAIL |
| S200-STATS-24 | ML-LR-1h | 3892 | +0.060 | -0.023 | -1.34 | -0.051 | +0.028 | +0.139 | 2 | FAIL |
| S200-STATS-25 | ML-GBM-1h | 3682 | +0.098 | +0.016 | 0.89 | -0.016 | +0.032 | +0.140 | 4 | FAIL |
| S200-STATS-26 | ML-LR-4h | 928 | +0.075 | +0.024 | 0.71 | -0.012 | +0.036 | +0.026 | 5 | FAIL |
| S200-STATS-27 | ML-GBM-4h | 777 | +0.033 | -0.029 | -0.79 | -0.024 | -0.005 | +0.029 | 4 | FAIL |
| S200-STATS-28 | ML-LR-1d | 210 | -0.073 | -0.167 | -2.55 | -0.160 | -0.006 | -0.015 | 2 | FAIL |
| S200-STATS-29 | ML-GBM-1d | 148 | -0.017 | -0.076 | -0.75 | -0.057 | -0.018 | -0.011 | 2 | FAIL |
| S200-STATS-30 | KMEANS-1d-REGIME | 103 | -0.042 | -0.135 | -1.13 | -0.139 | +0.004 | -0.003 | 1 | FAIL |
| S200-STATS-31 | ENS-4h-TREND | 670 | +0.112 | +0.021 | 0.43 | +0.018 | +0.003 | -0.121 | 3 | FAIL |
| S200-STATS-32 | ENS-1d-TREND | 70 | +0.051 | -0.024 | -0.19 | -0.005 | -0.019 | -0.033 | 3 | FAIL |
| S200-STATS-33 | ENS-4h-STAT | 281 | +0.052 | -0.033 | -0.46 | -0.035 | +0.002 | +0.058 | 4 | FAIL |

The same-day twin column is the brief's original control. It is reported only: it draws entries before the signal,
and it is visibly biased in the direction the coordinator described. Fades look good against it (S10 +0.27 R, S14
+0.22 R, S12 +0.15 R) and momentum rules look bad (S08 -0.32 R, S13 -0.28 R, S11 -0.25 R). Against the forward
control, no rule differs from random entry by more than +0.066 R.

## Reading

- **Costs are the result again.** Median charged cost is 0.088 R a trade, even at 1h-1d stops. The best gross rules
  (S07 Kalman 4h +0.127 R, S31 4h trend vote +0.112 R, S01 HMM low-vol +0.106 R, S25 GBM-1h +0.098 R) are all at or
  under 0.04 R net, with day-bootstrap 90% intervals spanning 0.
- **There is information, but too little to pay costs.** The 1h direction models reach an out-of-fold AUC of 0.54
  against a shuffled 0.50, stable over 6 folds. That is real predictability at 6 h, but it is worth about +0.03 R
  over a random entry, and the round trip takes about 0.08 R. This replicates M1/M1c from a different target:
  prediction is not edge after cost.
- **Shorts beat longs net in most rules** (S25 long -0.022 / short +0.050; S26 -0.043 / +0.093; S03 -0.072 / +0.087).
  This comes from the long swap (-$0.60/oz/night) and from the 2013-2015 gold bear market, not from a signal.
- Several 1d rules fire too rarely for the n >= 100 leg (S01, S02, S04, S05, S06, S09, S15, S32). Their nets are not
  positive at t >= 2 either, so a longer window would not change the verdict.

## Defects fixed after the first run (disclosed; no rule, threshold or parameter changed)

Explore ran three times. All three runs give bit-identical n, net and twin results for every rule not named below.
- Run 1: S18, S21, S23 and S33 had 0 decisions. The cause was `pct_rank`, which returned NaN for any window that held
  a missing value, and the realised-measure series have gaps. Fix: rank among the finite values of the window (needs
  at least half of them), applied to those four rules only (`pct_rank_finite`). The lookahead test was re-run: pass.
- Run 2: S18 and S21 still had 0 decisions. Each week's feature-day series includes a Friday 13:30-17:00 NY stub with
  no realised measure, so every 5-row window held a NaN. Fix: "the last 5 feature days" now means the last 5 days
  that have the measure. The lookahead test was re-run: pass.

None of these four rules had produced an outcome before its fix, so no outcome was seen and then changed. Separately,
an earlier leakage-only run was killed after a fork deadlock (threaded BLAS). The script now uses spawned workers and
one BLAS thread. That run read no outcome.

## K and retirement

K +33 (this lane's share of the sprint's 200; no extra variants; the defect re-runs are the same rules). Program K
with the sprint stays at the brief's 3,229 for this lane's count.

Retired as specified:
- 2-state HMM regime gates (1d, 4h);
- the RV-percentile and RV-ratio regime rules;
- the Kalman local-linear-trend cross and innovation fade (1h/4h/1d);
- VR and AC1 side-setting rules (1h/4h/1d);
- COMEX-window intraday momentum;
- realised skew, BNS jump, signed jump variation and MAX-lottery contrarian rules;
- fold-internal LR and GBM direction models on price statistics (1h/4h/1d);
- k-means 1d regimes;
- the equal-weight trend and stat votes.

Do not re-test these at neighbouring thresholds, horizons or feature sets. A model that pays would need new
information worth at least 0.1 R a trade at 1h-4h, or a cost per trade under about 0.03 R.
