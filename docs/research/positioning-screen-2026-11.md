# Positioning and flows (COT managed money, GLD holdings), 1-week holds: 2010-2017 screen result

Principal gold macro and flows researcher, run 2026-11-01. Rule frozen in `prereg-positioning-2026-11-01.md` at
commit **`f7fc8755`** (pre-registration and `hypotheses.md` rows, before any outcome); code frozen at **`af2eea16`**
(`scripts/research/positioning_screen.py`, `goldbot/research/positioning_screen.py`, tests) before its single run,
which ran unchanged. Inputs: `raw/xauusd_1m_dukascopy_2010..2017.parquet` (no 2018+ bar opened; asserted), CFTC
disaggregated `fut_disagg_txt_hist_2006_2016.zip` + `fut_disagg_txt_2017.zip`, SPDR GLD archive, all local in
`~/tmp/positioning/raw/` (sha256 prefixes in the prereg s.2), PIT-stamped by `goldbot.data.positioning` unchanged and
joined only through `asof_join` on `available_utc`, cut at 2018-01-01. Costs pinned `cor-2026-10-31.2`, ICM New
York: round trip $0.23/oz, stop overshoot $0.196/oz. Output (statistics only, not committed):
`~/tmp/positioning/screen.json`. No registry trial, holdout sealed. **K +3 (2,968 -> 2,971).**

## Verdict: all three variants FAIL by the frozen rule. COT/GLD timing rules at a 1-week hold are retired (R-19).

| Variant | n | Gross R (t) | Net R binding (SE) | Holm p | Net 1.5x | Net, no swap (not read) | Control mean / q95 | Years + (of 8) | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| P1_EXT (H-86): MM net % OI decile extremes, contrarian | 114 | +0.015 (0.16) | -0.081 (0.091) | 1.00 | -0.139 | +0.005 | -0.183 / -0.055 | 2 | **FAIL** (b, c, d, e, f, g) |
| P2_FLOW (H-87): -sign(weekly MM change), top third | 135 | +0.023 (0.29) | -0.051 (0.077) | 1.00 | -0.107 | +0.012 | -0.077 / +0.050 | 2 | **FAIL** (b, c, d, e, f, g) |
| P3_ETF (H-88): sign(GLD 5-day flow), top third | 214 | +0.014 (0.19) | -0.060 (0.071) | 1.00 | -0.115 | +0.003 | -0.007 / +0.104 | 2 | **FAIL** (b, c, d, e, f, g) |

Rule letters: (a) n >= 50, (b) Holm p < 0.05 on net, (c) gross t >= 2, (d) >= 5 of 8 years positive (>= 5 trades),
(e) net > 0 at 1.5x, (f) net >= +0.05 R, (g) net > the year- and side-matched random-entry control's q95. Every
variant passes only (a).

Mechanics as realised: median R $27-30/oz; 6.1-6.2 swap nights a trade; broker cost 0.010-0.011 R a trade; binding
swap 0.063-0.086 R a trade averaged over both sides; exits: stops 27-33%, time 67-73%; 0 truncated.

| Split | P1_EXT | P2_FLOW | P3_ETF |
|---|---|---|---|
| Long: n, gross, net | 82, +0.013, -0.117 | 67, +0.052, -0.086 | 98, +0.060, -0.090 |
| Short: n, gross, net | 32, +0.021, +0.010 | 68, -0.006, -0.017 | 116, -0.025, -0.036 |
| corr(side, sign of prior 5-day return) | +0.05 | -0.19 | +0.20 |

Per-year net R (n): P1 2011 -0.66 (3), 2012 -0.13 (22), 2013 -0.15 (29), 2014 +0.05 (7), 2015 -0.05 (21), 2016 +0.07
(29), 2017 -0.38 (3). P2 2010 -0.08 (16), 2011 -0.05 (18), 2012 -0.23 (21), 2013 -0.09 (14), 2014 +0.07 (18), 2015
+0.07 (16), 2016 -0.04 (15), 2017 -0.03 (17). P3 2010 +0.01 (22), 2011 -0.16 (34), 2012 -0.08 (15), 2013 +0.22 (32),
2014 -0.06 (26), 2015 -0.11 (29), 2016 -0.00 (38), 2017 -0.49 (18).

## Reading

1. **No gross signal.** Gross t is 0.16 / 0.29 / 0.19, cost-free and swap-free. The swap prior does not decide the
   verdict: swap-free net is +0.003 to +0.012 R, an order of magnitude under the +0.05 R floor.
2. **P1's control mean is -0.18 R** (its longs fall in 2012-2015, gold's bear market, where the year-matched random
   long loses). P1 beats it on the point estimate (91st percentile) but not at q95, and is negative in absolute
   terms. A contrarian "crowded short -> long" rule bought into a falling market and lost less than random longs; that
   is not an edge we can trade.
3. **P2 and P3 are not separate information.** P2's side correlates -0.19 with the prior week's return and P3's +0.20,
   consistent with the literature that positions and ETF flows follow price (Sanders, Boris & Manfredo 2004; the
   precious-metals thesis; the Deutsche Bank note). Neither beats random entries in its own years and sides.
4. **The Kang, Rouwenhorst & Tang liquidity sign does not show up in single-asset gold timing** at this hold (P2 gross
   +0.02 R). Their result is cross-sectional; this test does not contradict it, it just does not transfer.

## What goes to a confirm on 2018-01-01..2025-09-30

**Nothing.** No variant passed, so the holdout stays sealed and the line uses **0 registry trials**.

## Consequences (recorded in `hypotheses.md`)

- H-86, H-87, H-88: **failed**. Retired as **R-19**: COT managed-money decile-extreme contrarian, weekly-change
  contrarian, and GLD 5-day flow momentum as 1-week directional rules on XAUUSD. No re-test of other windows,
  thresholds, holds or the opposite signs without new evidence and a new id; each such variant counts in K.
- Not retired: the positioning columns as PIT *features or gates* in a model (`goldbot/features/positioning.py`),
  counted there; COT extremes as a *volatility* predictor (H-22); central-bank buying and data surprises (not tested).
- K: 2,968 + 3 = **2,971**.
- Owner flags unchanged (prereg s.8); no sizing question arises.
