# Sprint-200 lane `patterns` read: 0 of 34 pass explore, no confirm run, no survivors

Principal trade management researcher, 2026-11-02. Rule: `patterns-prereg.md` (commit `1de886fe`, pushed before any
bar was read). Code: `scripts/sprint200/patterns_screen.py` (commit `53e47b70`, also before the run). One explore run on
2 worker processes: `patterns_screen.py explore --raw <raw> --workers 2`. The output stays local in
`~/tmp/sprint200_patterns/explore.json` (sha256 `0284b507...ee58`) plus per-trade parquet; no market data is in the
repo. The last bar read was 2017-12-29 21:56 UTC. No 2018+ bar and no holdout bar was read. **Confirm was not run**,
because the prereg runs it only for explore-passers and there are none.

## Verdict

**FAIL on all 34.** No row reaches net t >= 2.0. The best net t is +1.30 (row 29, which also has n 90 < 100) and the
next is +1.27 (row 06). Under the pass rule the verdict holds whatever the control shows (see the control finding
below). K added: **+34**. Every row is retired for this sprint and no neighbours are run (prereg section 6).

## Results, explore 2010-2017 (R = net USD/oz over the stop distance; ICM cost of record cor-2026-10-31.2)

| ID | Pattern, context, TF/stop, exit | n | gross R (t) | net R (t) | control net R | net - control | years > 0 | Verdict |
|---|---|---|---|---|---|---|---|---|
| S200-PATTERNS-01 | ENG trend 1h/ST CH3 | 1181 | +0.143 (+1.91) | +0.017 (+0.23) | +0.131 | -0.114 | 5/8 | FAIL |
| S200-PATTERNS-02 | ENG trend 1h/ST BE2 | 1445 | -0.002 (-0.05) | -0.118 (-3.54) | +0.034 | -0.152 | 0/8 | FAIL |
| S200-PATTERNS-03 | ENG pdlevel 1h/ST FT2 | 452 | +0.039 (+0.59) | -0.053 (-0.79) | -0.019 | -0.034 | 3/8 | FAIL |
| S200-PATTERNS-04 | ENG trend 4h/ST CH3 | 354 | -0.038 (-0.36) | -0.098 (-0.94) | +0.134 | -0.232 | 2/8 | FAIL |
| S200-PATTERNS-05 | ENG trend LO/NYO 15m/A1.0 TS | 1640 | +0.094 (+1.93) | -0.105 (-2.11) | -0.070 | -0.035 | 2/8 | FAIL |
| S200-PATTERNS-06 | PIN trend at EMA20 1h/ST CH3 | 370 | +0.313 (+2.23) | +0.179 (+1.27) | +0.020 | +0.158 | 5/8 | FAIL |
| S200-PATTERNS-07 | PIN pdlevel 1h/ST BE2 | 438 | -0.031 (-0.55) | -0.143 (-2.44) | -0.367 | +0.224 | 2/8 | FAIL |
| S200-PATTERNS-08 | PIN trend 4h/ST PT | 231 | +0.013 (+0.15) | -0.046 (-0.55) | -0.188 | +0.142 | 3/8 | FAIL |
| S200-PATTERNS-09 | PIN LO/NYO 15m/A1.0 FT1.5 | 4277 | +0.039 (+2.08) | -0.154 (-7.95) | -0.275 | +0.121 | 0/8 | FAIL |
| S200-PATTERNS-10 | PIN stretch 1h/ST MR | 173 | -0.079 (-0.78) | -0.169 (-1.63) | -0.521 | +0.352 | 1/8 | FAIL |
| S200-PATTERNS-11 | IBB trend 1h/ST CH3 | 704 | +0.140 (+1.74) | +0.041 (+0.51) | +0.359 | -0.318 | 4/8 | FAIL |
| S200-PATTERNS-12 | IBB trend 4h/ST PT | 204 | -0.026 (-0.32) | -0.069 (-0.83) | +0.328 | -0.397 | 3/8 | FAIL |
| S200-PATTERNS-13 | IBB LO 15m/ST FT2 | 995 | +0.073 (+1.83) | -0.066 (-1.62) | +0.000 | -0.066 | 2/8 | FAIL |
| S200-PATTERNS-14 | FAKEY any 1h/ST FT2 | 773 | -0.010 (-0.19) | -0.161 (-3.08) | -0.222 | +0.060 | 1/8 | FAIL |
| S200-PATTERNS-15 | OB trend 1h/ST CH3 | 513 | +0.149 (+1.71) | +0.053 (+0.61) | +0.207 | -0.154 | 4/8 | FAIL |
| S200-PATTERNS-16 | OB pdlevel 1h/ST BE2 | 209 | +0.132 (+1.50) | +0.054 (+0.60) | +0.059 | -0.005 | 5/8 | FAIL |
| S200-PATTERNS-17 | OB trend 4h/A1.5 TS | 265 | +0.120 (+1.33) | +0.087 (+0.96) | +0.263 | -0.177 | 3/8 | FAIL |
| S200-PATTERNS-18 | R3 pdlevel 1h/ST BE3 | 489 | +0.007 (+0.11) | -0.079 (-1.27) | -0.000 | -0.079 | 3/8 | FAIL |
| S200-PATTERNS-19 | R3 trend 1h/ST CH3 | 1223 | +0.191 (+2.76) | +0.082 (+1.18) | +0.199 | -0.117 | 6/8 | FAIL |
| S200-PATTERNS-20 | R3 trend 4h/ST PT | 382 | +0.008 (+0.13) | -0.037 (-0.59) | +0.189 | -0.227 | 3/8 | FAIL |
| S200-PATTERNS-21 | R3 NYO 15m/A1.0 TS | 2596 | +0.046 (+1.18) | -0.143 (-3.59) | -0.184 | +0.041 | 1/8 | FAIL |
| S200-PATTERNS-22 | MARU any 1h/A1.0 CH3 | 1139 | -0.033 (-0.48) | -0.176 (-2.55) | +0.574 | -0.750 | 0/8 | FAIL |
| S200-PATTERNS-23 | MARU any 1h/A1.5 TS | 1252 | -0.010 (-0.28) | -0.083 (-2.36) | +0.407 | -0.490 | 2/8 | FAIL |
| S200-PATTERNS-24 | MARU trend 4h/A1.5 PT | 140 | +0.105 (+0.94) | +0.065 (+0.58) | +0.602 | -0.537 | 4/8 | FAIL |
| S200-PATTERNS-25 | MARU LO 15m/A1.0 AT2 | 660 | -0.018 (-0.24) | -0.263 (-3.33) | +0.098 | -0.360 | 2/8 | FAIL |
| S200-PATTERNS-26 | MARU-PD any 1h/A1.0 CH3 | 380 | -0.067 (-0.59) | -0.212 (-1.85) | +0.717 | -0.929 | 1/8 | FAIL |
| S200-PATTERNS-27 | MARU-F any 1h/A1.0 FT1 | 485 | +0.050 (+1.11) | -0.067 (-1.43) | -0.330 | +0.264 | 4/8 | FAIL |
| S200-PATTERNS-28 | 3S trend 1h/A1.5 CH3 | 125 | +0.140 (+0.92) | +0.058 (+0.39) | +0.568 | -0.510 | 4/8 | FAIL |
| S200-PATTERNS-29 | 3S any 4h/A1.5 TS | 90 | +0.299 (+1.47) | +0.265 (+1.30) | +0.463 | -0.197 | 4/8 | FAIL (n < 100) |
| S200-PATTERNS-30 | NRB7 trend 1h/ST CH3 | 576 | +0.043 (+0.36) | -0.113 (-0.93) | +0.322 | -0.435 | 3/8 | FAIL |
| S200-PATTERNS-31 | NRB4 trend 4h/ST BE3 | 307 | +0.094 (+1.10) | +0.017 (+0.20) | +0.507 | -0.490 | 5/8 | FAIL |
| S200-PATTERNS-32 | PIN>ENG any 1h/ST CH3 | 317 | -0.022 (-0.23) | -0.119 (-1.25) | -0.035 | -0.084 | 4/8 | FAIL |
| S200-PATTERNS-33 | ENG daily pullback 1h/ST VT2 | 1024 | -0.041 (-0.96) | -0.149 (-3.41) | -0.108 | -0.041 | 0/8 | FAIL |
| S200-PATTERNS-34 | PIN at PWH/PWL 4h/ST CH3 | 75 | -0.248 (-1.54) | -0.309 (-1.92) | -0.618 | +0.309 | 3/8 | FAIL (n < 100) |

The stress line (0.70 per stop exit) is in `explore.json`; it only lowers these numbers.

## Reading

1. **Costs are the whole story at 15m.** The 15m rows have gross t up to +2.08 (row 09, n 4,277: +0.039 R gross) and lose
   0.10-0.26 R net, because a ~$1.7 stop pays ~0.15-0.2 R in round trip and overshoot. A pattern with gross +0.04 R
   cannot carry the cost of record at intraday stop sizes. This repeats H-82 and RS-1..3.
2. **1h trend-following rows with a chandelier exit (01, 06, 11, 15, 19) are the only cluster with positive gross**
   (+0.14..+0.31 R, gross t 1.7-2.8). Net of costs it shrinks to +0.02..+0.18 R, t <= 1.27. Their profit is in the short
   side (gold's 2011-2015 bear market). On every one of these rows except 06, the same-side controls earn as much or
   more. That is regime drift, not an entry edge, and the "trend" filter is what selects it.
3. **Exits.** Chandelier rows end at the stop 94-99% of the time (trailed stops). BE>2/3 rows end at the stop 72-79% of
   the time and are worse than the CH3 or FT rows on the same pattern (ENG 1h: BE2 -0.118 vs CH3 +0.017). Moving to
   break-even at +1 R costs the overshoot on many scratch exits and cuts the right tail. Partials (PT) at 4h are about 0
   net. No exit turned a pattern positive at t >= 2.

## Finding for the director and the evaluator: the brief's matched control leaks the future

Drawing the control "same UTC day x session x side" lets a draw land **before** the signal bar. Its side was set by the
signal, which happened later that session, so that control trade enters ahead of a move in a direction already known.
A descriptive check (explore data only, no verdict, not a trial: `scripts/sprint200/patterns_ctrl_diag.py`, up to 4 draws per trade
split by position)
measured it:

| Row | Control draws before the signal: gross R | after the signal: gross R |
|---|---|---|
| 22 MARU 1h CH3 | **+1.80** (n 1,829) | -0.02 (n 2,695) |
| 26 MARU-PD 1h CH3 | **+2.49** (n 539) | -0.13 (n 953) |
| 06 PIN 1h CH3 | -0.02 (n 524) | +0.24 (n 948) |
| 32 PIN>ENG 1h CH3 | +0.22 (n 594) | -0.01 (n 674) |

The effect is large and it cuts both ways. Momentum and breakout signals face an inflated control, so they fail the
control leg even if they are real. Reversal signals face a deflated control (rows 07, 09, 10, 27, 34 "beat" control by
+0.12..+0.35 R while losing money). **Any lane that reports a pass on the control leg of a reversal or fade rule should
be re-read with an after-signal-only control (or the same time of day on other days).** None of my verdicts depends on
it, because every row fails the net t leg first. Recommended amendment for the next round, frozen before its look:
draw controls only from bars at or after the signal's close in the same session, or from the same TF slot on 20 random
other days, with the side unchanged.

## What not to re-test

Rows 01-34 as specified (K counted). In general: candle patterns with stops under ~$3 at the ICM cost of record (cost
>= 0.1 R before any edge); break-even at +1 R on these entries; and trend-filtered 1h patterns judged on 2010-2017 alone
(that window's bear market supplies the short-side gross).

## Integrity

- The prereg (1de886fe) and the code (53e47b70) were committed and pushed before the run. One explore run; no edit
  after the look. The control diagnostic is descriptive and changes no rule.
- The walk was checked on synthetic minutes before the run: target, stop-first in the same minute, gap through the
  stop, BE from the next minute, partial, time exit, chandelier update at the TF close, short by negation, ENG
  detection in both views.
- Costs: `goldbot.research.arms.screen_net_r`, ICM, pinned `cor-2026-10-31.2`; swap from `SwapSpec.shipped()`.
