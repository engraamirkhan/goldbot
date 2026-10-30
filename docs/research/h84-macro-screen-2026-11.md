# H-84 macro-driven 4h/1d direction: 2010-2017 screen result

Principal gold macro and flows researcher, run 2026-10-30. Rule frozen in `prereg-2026-11-03-h84-macro.md` at commit
**`d4b8499d`** (pre-registration, `hypotheses.md` status and `scripts/research/h84_macro_screen.py` in one commit,
before any outcome). The script ran unchanged from that commit. Inputs: `raw/xauusd_1m_dukascopy_2010..2017.parquet`
(no 2018+ file opened; loader asserts the cut), `macro-v1` `macro_fred.parquet` sha256 `47473d2c...` cut at
`available_utc` < 2018-01-01. Output (statistics only, not committed): `~/tmp/h84/result.json`. No registry trial,
no holdout. **K +4.**

## Verdict: all four variants FAIL by the frozen rule. Plan line 1 (H-84) is retired.

| Variant | n | Gross R (t) | Net R binding (SE) | Holm p | Net 1.5x | Net, no swap (not read) | Years + (of 8) | Long gross / net (n) | Short gross / net (n) | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| V1_RY: -sign(20d chg DFII10) | 221 | -0.055 (-0.59) | -0.223 (0.091) | 1.00 | -0.343 | -0.081 | 1 | +0.015 / -0.265 (124) | -0.144 / -0.170 (97) | **FAIL** (b, c, d, e) |
| V2_USD: -sign(20d % chg DTWEXBGS) | 215 | +0.045 (0.47) | -0.112 (0.092) | 1.00 | -0.231 | +0.018 | 2 | +0.136 / -0.178 (98) | -0.031 / -0.057 (117) | **FAIL** (b, c, d, e) |
| V3_H06: H-01 slow gated by both | 93 | +0.125 (0.84) | -0.054 (0.144) | 1.00 | -0.171 | +0.097 | 3 | +0.340 / +0.045 (53) | -0.159 / -0.185 (40) | **FAIL** (b, c, d, e) |
| V4_VRP: long in top third of 252-day VRP | 113 | +0.077 (0.62) | -0.218 (0.122) | 1.00 | -0.365 | +0.053 | 1 | +0.077 / -0.218 (113) | none | **FAIL** (b, c, d, e) |

Rule letters: (a) n >= 60, (b) Holm p < 0.05 on net, (c) gross t >= 2, (d) >= 5 of 8 years positive, (e) net > 0 at
1.5x. Every variant passes only (a).

Mechanics as realised: median R $27/oz (1.5 x ATR(1d)); mean hold 50-57 four-hour bars (about 9 trading days),
11.7-13.4 swap nights; round-trip cost 0.025-0.028 R a trade; binding swap 0.13-0.15 R a trade averaged over both
sides (0.27 R for the long-only V4). Exits: stops 57-62%, targets 25-33%, time 8-15%; 0 trades truncated at the
window end.

Per-year net R (binding; n in brackets):

| Year | V1_RY | V2_USD | V3_H06 | V4_VRP |
|---|---|---|---|---|
| 2010 | -0.153 (28) | -0.188 (30) | **+0.230** (10) | +0.440 (7, < 10: not counted) |
| 2011 | **+0.234** (35) | **+0.534** (34) | **+0.311** (15) | -0.106 (15) |
| 2012 | -0.613 (29) | -0.303 (26) | +0.287 (5, < 10: not counted) | -0.004 (11) |
| 2013 | -0.048 (27) | **+0.069** (24) | **+0.092** (15) | -0.617 (18) |
| 2014 | -0.421 (25) | -0.127 (24) | -0.366 (12) | -0.239 (11) |
| 2015 | -0.077 (22) | -0.366 (27) | -0.460 (13) | -0.396 (20) |
| 2016 | -0.110 (25) | -0.421 (24) | -0.072 (11) | **+0.016** (24) |
| 2017 | -0.641 (30) | -0.285 (26) | -0.303 (12) | -0.681 (7, < 10) |

## Reading

1. **The verdict does not depend on the swap prior.** The binding swap (today's -60 USD/lot long) overcharges
   2010-2017, but condition (c), gross t >= 2, is cost-free and fails for every variant (t -0.59 to +0.84). Even the
   swap-free net, which the rule does not read, is at most +0.10 R with an SE of ~0.14 R.
2. **V1, the real-yield sign rule, has the wrong sign even before the 2022 break** (gross -0.055 R; shorts -0.14 R).
   A 20-day real-yield change does not predict the next 5-20 days of gold on these barriers, in the regime where the
   contemporaneous relation was strongest. This confirms R-08 with in-house evidence.
3. **V3 (H-06) reproduces H-01's shape**: gross +0.125 R (H-01 was +0.166 R over 2010-2025-09 unconditioned), all of
   it from longs (+0.34 R gross, n 53), shorts negative. The gate cuts the sample to 93 trades without raising the
   gross enough to reach t 2. The long-only split was not pre-registered as a variant. **It is not a new
   hypothesis**: it is one of eight side/variant cells, chosen after seeing the data, and it nets only +0.045 R.
4. **V4 (VRP)** is long-only, so it pays the full long swap (0.27 R a trade) on a gross of +0.077 R (t 0.62).
5. **2011 is the only year in which three of four variants are positive.** It was gold's trend top, which fits the
   reading that these rules are slow trend with extra conditions, not separate macro information.

## What passes to a confirm on 2018-01-01..2025-09-30

**Nothing.** No variant goes to the confirm in s.6 of the pre-registration, so this line uses **0 registry trials**.
The DTWEXBGS first-release re-check (A1/A2) is not needed, because no V2 or V3 survivor depends on it.

## Consequences (recorded in `hypotheses.md`)

- H-84 status: **failed** (0 of 4). Retired as **R-16**: daily-signal 4h/1d direction rules from 20-day real-yield or
  broad-dollar changes, their joint gate on H-01, and the top-third GVZ-HAR variance premium (long), each on H-01's
  barrier geometry. None returns without new evidence and a new id. Re-parameterising lookbacks, thresholds or
  barriers on the same inputs counts as a re-test.
- **Not retired, and not tested here:** macro levels as *regime* inputs to a model, COT/ETF flows, central-bank
  buying, and data surprises. If they enter at all, they enter as gate primitives in discovery v2 (bet 3), counted in
  K_v2.
- K: ~1,256 + 4 = **~1,260**.
- Owner flags are unchanged (`prereg-2026-11-03-h84-macro.md` s.8). No sizing question arises, because nothing
  survived.
