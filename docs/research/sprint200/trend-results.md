# Sprint-200 lane TREND: results (34 strategies, 0 survivors)

Strategy researcher (principal), 2026-11-02. Pre-registration `trend-prereg.md` (commit `5a235f38`, before any
look; amendment 1 and the script in `72543db0`, before any run). Script `scripts/sprint200/trend_screen.py`. Cost of
record `cor-2026-10-31.2`, ICM upper bounds (round trip max($0.23, ci90 upper), overshoot max($0.12, ci90 upper) on
stop and trail exits), shipped swap (long -$0.60/oz/night). Explore 2010-2017, confirm 2018-01-01..2025-09-30 run
once, for the one explore passer only. Nothing at or after 2025-10-01 was read (loader assertion).

**Verdict: 0 of 34 pass confirm. No arm, no rule code, no `exploration_arms.yaml` entry.** One strategy passed
explore (03 H1-DON48-D1: net +0.206 R, t 2.68, leak-free control +0.115 R) and failed confirm (net +0.070 R, t 0.99,
DSR 0.003 at K 3,233; 6 of 8 years positive). With 34 tests, about 1.7 nominal explore passes are expected by
chance alone; one, then no confirm, is what a null looks like.

## Disclosures (integrity)

- **Run order:** prereg commit -> script commit -> parity check (synthetic brackets, no strategy outcome) -> count-only
  run (signal counts, no outcome) -> one explore run -> one confirm run (03 only). No rerun, no parameter changed.
- **Code change after the script commit, before any outcome:** `s28` crashed in the count-only run (a read-only numpy
  view assigned in place); fixed with `.copy()`. No rule change.
- **Spec defect found at the count-only step, left as registered:** 34 LINREG-1H is unsatisfiable. For an OLS slope
  on n = 48 points, t^2 = R^2 (n - 2) / (1 - R^2), so at the t = 3 crossing R^2 is 0.164 and can never be >= 0.6
  (that needs t >= 8.3). It reports n = 0 (FAIL-COUNT) and is still counted in K. It was not repaired: a repaired
  rule would be a new trial.
- **Controls:** the pass rule uses the pre-registered leak-free control (twins only at or after the real fill, same
  session instance, side and exits). The brief's literal same-day control (whole session, pre-signal minutes
  included) is reported beside it as "look-ahead ctrl"; for momentum rules it runs +0.2 to +0.3 R above the leak-free
  one because a pre-signal twin given the signal's side carries the move that triggered it (coordinator ruling
  2026-11-02, same finding as the patterns lane).
- **Engine parity:** against the harness `triple_barrier_next_open_1m` on 424 synthetic 1h brackets (2012 H1): 421
  identical, 3 differ, all stop exits where a minute gapped through the stop (filled at that minute's open here, at
  the stop in the harness: this engine is the more conservative).
- **K:** +34 for this lane (34 specs, no other variant). DSR K used: 3,233 (3,029 + 6 lanes x 34, upper bound).

## All 34 (explore 2010-2017; confirm only where explore passed)

gross R (t) is before costs and swap; cost R is round trip + overshoot - swap, per trade, in R; stress = $0.70
overshoot; years+ = explore years with net mean > 0.

| id | name | n | gross R (t) | net R | t | leak-free ctrl excess | look-ahead ctrl excess | cost R | stress net R | years+ (of 8) | explore | confirm |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 01 | H4-DON30-TR | 329 | +0.188 (2.26) | +0.047 | 0.58 | -0.025 | -0.203 | 0.142 | +0.009 | 4 | FAIL | not run |
| 02 | H4-DON20-D1 | 237 | +0.169 (1.69) | +0.048 | 0.48 | -0.031 | -0.204 | 0.121 | +0.024 | 5 | FAIL | not run |
| 03 | H1-DON48-D1 | 449 | +0.316 (4.08) | +0.206 | 2.68 | +0.115 | -0.224 | 0.110 | +0.126 | 7 | PASS | FAIL: n 497, net +0.070, t 0.99, DSR 0.003, 6/8 yrs, ctrl +0.047 |
| 04 | H1-DON24-ATRX | 1395 | +0.162 (3.87) | +0.042 | 1.01 | +0.105 | -0.290 | 0.120 | -0.063 | 6 | FAIL | not run |
| 05 | H4-KELT | 398 | +0.102 (1.24) | -0.031 | -0.39 | -0.006 | -0.303 | 0.133 | -0.083 | 3 | FAIL | not run |
| 06 | H4-POSTBREAK-PB | 246 | +0.004 (0.05) | -0.092 | -1.11 | -0.008 | -0.151 | 0.096 | -0.125 | 1 | FAIL | not run |
| 07 | ASIA-LON-1H | 971 | +0.043 (0.79) | -0.023 | -0.41 | -0.018 | -0.210 | 0.066 | -0.098 | 2 | FAIL | not run |
| 08 | LON-OR60-1H | 1710 | +0.071 (1.54) | -0.008 | -0.18 | +0.022 | -0.218 | 0.079 | -0.103 | 4 | FAIL | not run |
| 09 | NY-OR60-D1 | 789 | -0.006 (-0.18) | -0.062 | -1.84 | +0.007 | -0.138 | 0.056 | -0.093 | 3 | FAIL | not run |
| 10 | SGE-OR | 1354 | +0.081 (3.38) | -0.019 | -0.79 | +0.006 | -0.219 | 0.101 | -0.062 | 2 | FAIL | not run |
| 11 | PDHL-1H-D1 | 649 | +0.074 (1.08) | +0.002 | 0.03 | +0.030 | -0.202 | 0.072 | -0.074 | 3 | FAIL | not run |
| 12 | PWHL-4H | 358 | +0.116 (1.55) | -0.010 | -0.13 | -0.008 | -0.197 | 0.126 | -0.043 | 3 | FAIL | not run |
| 13 | ASIA-SQZ-15M | 150 | +0.227 (1.07) | +0.104 | 0.49 | +0.023 | -0.088 | 0.123 | -0.062 | 4 | FAIL | not run |
| 14 | LON-HL-NY | 955 | -0.021 (-0.59) | -0.080 | -2.25 | -0.018 | -0.117 | 0.060 | -0.120 | 1 | FAIL | not run |
| 15 | H4-BBW | 183 | +0.289 (2.22) | +0.149 | 1.18 | -0.009 | -0.189 | 0.139 | +0.092 | 4 | FAIL | not run |
| 16 | NR4ID-1H | 554 | +0.081 (2.46) | +0.059 | 1.80 | +0.031 | -0.087 | 0.022 | +0.051 | 7 | FAIL | not run |
| 17 | H1-ATRCOMP | 550 | +0.032 (0.49) | -0.064 | -0.98 | +0.018 | -0.192 | 0.095 | -0.136 | 2 | FAIL | not run |
| 18 | H4-INSIDE-D1 | 380 | -0.004 (-0.06) | -0.088 | -1.35 | +0.000 | -0.103 | 0.084 | -0.122 | 2 | FAIL | not run |
| 19 | H1-SHOCK-CONT | 998 | +0.110 (2.73) | -0.003 | -0.08 | +0.140 | -0.219 | 0.113 | -0.075 | 4 | FAIL | not run |
| 20 | ITS-AL2NY | 411 | +0.124 (2.27) | +0.092 | 1.66 | +0.059 | +0.070 | 0.033 | +0.075 | 4 | FAIL | not run |
| 21 | ITS-A2L | 597 | +0.056 (1.63) | +0.020 | 0.58 | +0.004 | +0.004 | 0.036 | +0.005 | 4 | FAIL | not run |
| 22 | BIGDAY-NEXT | 80 | +0.072 (0.52) | +0.049 | 0.36 | -0.028 | -0.031 | 0.023 | +0.039 | 5 | FAIL-COUNT | not run |
| 23 | ITS-NOON-D1 | 424 | -0.005 (-0.14) | -0.042 | -1.14 | +0.007 | -0.168 | 0.037 | -0.052 | 1 | FAIL | not run |
| 24 | MTF-EMA-1H | 491 | +0.201 (2.65) | +0.085 | 1.11 | +0.071 | -0.177 | 0.117 | +0.006 | 5 | FAIL | not run |
| 25 | MTF-RSI-4H | 230 | -0.137 (-1.55) | -0.221 | -2.54 | -0.038 | -0.145 | 0.085 | -0.259 | 2 | FAIL | not run |
| 26 | MTF-15M | 1769 | +0.016 (0.55) | -0.096 | -3.13 | +0.003 | +0.032 | 0.112 | -0.201 | 3 | FAIL | not run |
| 27 | MACD-4H-D1 | 304 | +0.164 (1.95) | +0.056 | 0.68 | +0.018 | -0.076 | 0.109 | +0.019 | 5 | FAIL | not run |
| 28 | ADX-ONSET-4H | 207 | +0.252 (2.26) | +0.141 | 1.28 | +0.008 | -0.025 | 0.110 | +0.107 | 5 | FAIL | not run |
| 29 | ATREXP-1D | 66 | -0.106 (-0.79) | -0.199 | -1.52 | -0.048 | -0.058 | 0.093 | -0.213 | 0 | FAIL-COUNT | not run |
| 30 | PB-D1EMA20-1H | 138 | -0.123 (-1.39) | -0.195 | -2.20 | -0.023 | +0.010 | 0.071 | -0.212 | 2 | FAIL | not run |
| 31 | PB-4H-EMA20 | 607 | +0.145 (2.69) | +0.044 | 0.83 | +0.006 | -0.040 | 0.101 | +0.015 | 5 | FAIL | not run |
| 32 | RSI2-1H | 2361 | +0.041 (3.15) | -0.012 | -0.93 | +0.019 | +0.134 | 0.054 | -0.032 | 3 | FAIL | not run |
| 33 | SUPERTREND-1H | 758 | +0.078 (1.60) | -0.004 | -0.09 | +0.011 | +0.020 | 0.082 | -0.020 | 4 | FAIL | not run |
| 34 | LINREG-1H | 0 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | FAIL-COUNT | not run |

## What this says (and does not)

1. **A gross trend premium exists at 1h-4h in gold, and costs take it.** 11 of 34 have gross t >= 2 in explore
   (01, 03, 04, 10, 15, 19, 20, 24, 28, 31, 32); the strongest families are channel breaks with a chandelier trail
   (03 gross +0.316 R, t 4.08; 04 +0.162 R, t 3.87). 03's gross edge survives out of sample at about half the size
   (confirm gross +0.165 R, t 2.29) but the ICM cost of about 0.10-0.12 R a trade (stops of $5-9) plus the long swap
   leaves +0.07 R, t 0.99. This is the R-06 tsmom result again, through a different trigger and exit: the trend
   premium on XAUUSD 1h-4h is about 0.15-0.3 R gross, and a CFD round trip with overshoot and swap costs 0.1-0.15 R.
2. **Intraday session breakouts have no gross edge** (07, 08, 09, 11, 13, 14, 17, 18, 23: gross |t| < 1.6), the
   fifth independent read after R-02, 15m ORB, GS-C2 and R-17. Close the family at 15m-1h.
3. **Pullback entries lose gross** (06, 25, 30) or match their random twins (31: excess +0.006 R). The daily-EMA20
   retest (30, the owner's example) is gross -0.12 R (t -1.39) on 138 trades.
4. **Session TSMOM** (20 Asia+London -> NY: gross +0.124 R, t 2.27, net +0.092 R, t 1.66, control +0.059 R) is the
   only rule whose net is close to its gross (cost 0.03 R: wide 0.5 ATRd stops, no swap). It failed explore on t,
   4 of 8 years. Not a confirm candidate under the frozen rule; noted, not re-tested.

## Ranked implications (for the research director; owner-only items flagged)

1. **Do not spend more trials on 1h-4h trend triggers at today's cost.** The trigger is not the problem; the cost
   is. Any next test of this family needs a cost change first: a swap-free, lower-commission venue for the trend
   book (owner decision: spending and instruments, same as the decision memo's option C), or a stop/horizon design
   where cost is below 0.05 R (stops of $15-20+, i.e. 4h-1d holds, which brings the swap back for longs).
2. **Short-side swap asymmetry** (shorts pay no swap) is worth one pre-registered look only if a new trend trial is
   funded: confirm long share for 03 was 71% and longs pay $0.60/oz a night.
3. **Retire as rules** (do not re-test without new evidence): 07-14, 16-18 session and compression breakouts at
   15m-1h, 06/25/30/31 pullbacks, 32 RSI(2), 33 Supertrend flip, 21-23 session TSMOM variants, 26 MTF 15m, 29
   ATR-expansion 1d (n 66), 22 big-day continuation (n 80), 34 (spec defect; a working regression-trend rule would
   be a new trial).
