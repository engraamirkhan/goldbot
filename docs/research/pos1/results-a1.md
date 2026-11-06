# POS-1 A1 results (TREND-XASSET): 0 of 16 pass EXPLORE; the line is closed

Research director, 2026-11-06. Pre-registration: `prereg.md` + `amendment-a1.md` (commit 44568243, blob recorded
in each JSON). One EXPLORE read (LEARN-OLD 2003-05 + LEARN 2010-17); read claims in `state/position_reads.jsonl`.
Summary JSONs (no trade rows, no prices): `explore-a1-book.json`, `explore-a1-metal.json`, `explore-a1-gold.json`.
Confirm set `confirm-set-a1.json`: **m = 0**. CONFIRM (2018-01..2025-09) and TEST-OLD were **not read** and stay
unread. Holdout untouched.

## Verdict (venue A = ICM CFD with financing; sums in units of starting equity, no compounding)

GBP/yr per GBP 1k = net / Y_learn x 1,000 at the spec's own sizing (10 %/yr vol per market or book; Turtle/033 at
0.5 % risk per unit). PF is the daily profit factor.

| id | net | gross | cost | financing | net SR | gross t | net t | PF | yrs + | round trips | GBP/yr per 1k | null excess | failed checks |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 026A book TSMOM 1/3/12 | +0.102 | +0.535 | 0.064 | -0.369 | +0.09 | +1.70 | +0.32 | 1.02 | 6/10 | 1408 | +11 | +0.534 | t |
| 027A book Turtle 55/20 | -0.203 | +0.078 | 0.025 | -0.256 | -0.21 | +0.29 | -0.76 | 0.96 | 3/10 | 328 | -22 | +0.075 | net, t, years, drift |
| 057A book inverse-vol | +0.094 | +0.526 | 0.064 | -0.369 | +0.09 | +1.66 | +0.30 | 1.02 | 6/10 | 1408 | +10 | +0.524 | t |
| 025 metals TSMOM | +0.407 | +0.556 | 0.026 | -0.123 | +0.27 | +1.21 | +0.89 | 1.06 | 6/10 | 330 | +42 | +0.535 | t |
| 060 metals rotation | +0.261 | +0.333 | 0.005 | -0.068 | +0.31 | +1.27 | +0.99 | 1.07 | 4/10 | 27 | +27 | +0.102 | t, years, RT |
| 001 gold TSMOM-12M | +0.436 | +0.524 | 0.003 | -0.085 | +0.40 | +1.61 | +1.35 | 1.08 | 6/10 | 18 | +45 | +0.502 | t, RT |
| 002 gold TSMOM 1/3/12 | +0.106 | +0.166 | 0.006 | -0.054 | +0.12 | +0.65 | +0.41 | 1.03 | 5/10 | 167 | +11 | +0.154 | t, years |
| 004 gold Turtle | -0.004 | +0.007 | 0.001 | -0.011 | -0.02 | +0.10 | -0.06 | 1.00 | 4/10 | 41 | -0 | +0.001 | net, t, years |
| 006 gold SMA 50/200 | +0.009 | +0.082 | 0.004 | -0.070 | +0.01 | +0.25 | +0.03 | 1.00 | 5/10 | 18 | +1 | +0.071 | t, years, drift, RT |
| 008 gold Faber 200d | +0.268 | +0.334 | 0.002 | -0.064 | +0.34 | +1.31 | +1.06 | 1.08 | 5/10 | 40 | +28 | +0.058 | t, years |
| 015 gold TSMOM vs bills | +0.393 | +0.467 | 0.001 | -0.072 | +0.48 | +1.86 | +1.58 | 1.12 | 5/10 | 10 | +41 | +0.145 | t, years, RT |
| 016 gold six-signal | +0.088 | +0.132 | 0.005 | -0.039 | +0.13 | +0.63 | +0.42 | 1.03 | 4/10 | 172 | +9 | +0.115 | t, years |
| 033 haven after crash | +0.025 | +0.031 | 0.001 | -0.005 | +0.21 | +0.85 | +0.69 | 1.07 | 6/10 | 51 | +3 | +0.008 | t |
| 055 gold ensemble | +0.180 | +0.236 | 0.002 | -0.054 | +0.24 | +1.01 | +0.78 | 1.05 | 5/10 | 60 | +19 | +0.218 | t, years |
| 056 ensemble long/flat | +0.156 | +0.205 | 0.002 | -0.047 | +0.25 | +1.04 | +0.80 | 1.07 | 4/10 | 32 | +16 | -0.002 | t, years, null |
| 045 vol-managed long | +0.293 | +0.383 | 0.003 | -0.087 | +0.31 | +1.25 | +0.96 | 1.06 | 7/10 | 2 | +30 | +0.009 | t, drift, RT |

No variant reaches t >= 2.0, so the confirm and TEST-OLD columns are empty by rule ("not read").

## What it says

1. **The cross-asset book has a gross trend edge but CFD financing removes 70 % of it.** 026A: gross +0.535 (gross
   SR 0.50, t 1.70), financing -0.369 (about -3.9 %/yr: DTB3/OECD differentials plus the 2.5 %/yr default markup on
   every leg), net SR 0.09. Even gross, the book runs at half the literature prior (SR ~1.0) in 2004-05 + 2010-17.
2. **Gold alone:** the slow month-end rules (001, 015) are the best net (SR 0.40-0.48, t 1.35-1.58) but trade 10-18
   round trips in 10 years: untestable at the bar, as the prereg's power table said (P_explore 0.23).
3. **Breakout (Donchian/Turtle) is the worst form** on both the book (027A, net -0.20) and gold (004, flat).
4. **Costs:** spread + commission are trivial at this horizon (0.001-0.064 over 10 years); the binding cost of a
   multi-day CFD hold is the overnight swap. Measured swaps exist only for gold (0.74 %/yr markup).

## Re-derived (Rule 6)

- 026A net +0.102 = gross 0.535 - cost 0.064 + financing -0.369, recomputed from the engine's daily book file; SR 0.09.
- POS1-001 gross re-derived from the raw gold 1m files by an independent, simpler path (month-end sign(r252), 10 %/yr
  EWMA vol scaling, close-to-close, no band, no stop): **+0.543** vs the engine's **+0.524** over the EXPLORE window.

## Kill and K

- Kill rule A1.4 met (026A and 057A cannot reach confirm). **TREND-XASSET is closed 2026-11-06**: not re-run,
  widened, re-parameterised (e.g. with other markups or vol targets) or renamed. Reopening needs new mechanism
  evidence and a new card. The full-U_16 originals 026/027/057 stay superseded and unread.
- K: 16 EXPLORE reads recorded in the trial registry (family `pos1:trend-xasset`); line K for deflation 19.
- Owner's box break + MFI (mandate B) is **not started**: the box family already read gross +0.012 R / net -0.199 R
  per trade on 2018-2025 (n 5,355, `prereg-exits-adaptive-2026-10-26.md` s.8); gross below 2x cost fails Rule 2.
