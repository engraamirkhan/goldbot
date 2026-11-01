# Meta-label filter screen ML-M1 / ML-M2 on 2010-2017: both FAIL (ML researcher, 2026-11-01)

Rule frozen in `prereg-ml-meta-label-2026-11-01.md` at commit **`1eac5cb6`** (before any outcome), amendment 1 and
code frozen at **`ec68e028`** (`goldbot/research/meta_label_screen.py`, `scripts/research/meta_label_screen.py`,
`tests/research/test_meta_label_screen.py`) before the single run, which ran unchanged (71 s, 4 workers). Inputs local
only: Dukascopy 1m 2010-2017, `raw/positioning/positioning.parquet`, `calendar-v1` (all cut at 2018-01-01, asserted);
costs `cor-2026-10-31.2` ICM charged cells + long swap $0.60/oz/night. Output (statistics only, not committed):
`~/tmp/meta_label/result.json`. No registry trial; 2018+ and the holdout sealed. **K +2.**

## Verdict

| Leg (prereg s.6) | M1: I0 decisions both ways (41,862) | M2: I2B box break (3,102) |
|---|---|---|
| (a) power: candidates / top decile | 41,862 / 4,190: yes | 3,102 / 313: yes |
| (b) top-decile net mean R (90% LB) | **+0.016 (-0.034)**: no | **-0.513 (-0.655)**: no |
| (c) vs year- and side-matched random selection | -0.095 (q97.5 -0.054), p 0.0005: yes | -0.795 (q97.5 -0.626), p 0.0005: yes |
| (d) years with top-decile net > 0 | 5 of 8: yes | 1 of 8: no |
| (e) forward leg 2012-2017, top-decile net | **-0.015** (n 3,110; control -0.111, p 0.0005): no | -0.584 (n 236): no |
| Validity: shuffled-label model vs control | p 0.063 (not VOID; above 0.025) | p 0.945 |
| Lookahead test (12 cuts x 50 rows x 39 columns) | 0 mismatches | 0 mismatches |
| **Verdict** | **FAIL** (b, e) | **FAIL** (b, d, e) |

## Diagnostics (not in the verdict)

| | M1 | M2 |
|---|---|---|
| Pool net / gross mean R | -0.163 / +0.011 | -0.794 / -0.042 |
| Top-decile gross mean R | **+0.146** | -0.042 |
| Top-decile net at 1.5x cost | -0.048 | -0.749 |
| Long share: top decile / pool | 11% / 50% | 42% / 52% |
| Stop exits in the top decile | 61% | 68% |
| One-at-a-time book of the top decile | n 2,395, +0.024 R | n 303, -0.487 R |
| Per trade at 0.01 lot | +$0.025 (GBP 0.02) | -$0.44 |
| OOF AUC (net > 0) | 0.537 | 0.508 |
| Decile net R, bottom -> top | -0.39, -0.32, -0.24, -0.21, -0.19, -0.14, -0.07, -0.04, -0.02, +0.02 (monotone) | not monotone (-1.33 ... -0.48, -0.85, ... -0.51) |
| Top gain columns | side, pdl_dist_atr, hour_utc, COT chg4, em_atr_cost, h4 trend, ret_96, GLD chg5, COT z52, GLD chg20 | em_atr_cost, jump_z, COT z52, atr ratios |

## Reading

1. **M1 is the first model in the program whose ranking beats random selection out of sample, and it still does not
   pay.** Matched on year and side, the top decile is +0.11 R better than random picks of the same longs and shorts,
   and the strictly forward leg shows the same gap (+0.10 R, p 0.0005). Its gross mean is +0.15 R. But the I0 bracket
   (1.5 x ATR15 stop, about $2.5) costs about 0.13 R a trade, so the net is +0.016 R, the 90% bound is negative, and
   the forward net is -0.015 R. At 0.01 lot that is 2 pence a trade. By the frozen rule this is a FAIL.
2. **Most of the "edge" is structural, not directional.** 89% of the top decile is short. The model learned that
   longs pay the $0.60 swap (about 0.24 R on a $2.5 stop) and that low cost-in-R hours help (`side`, `hour_utc`,
   `em_atr_cost` among the top gains). The per-year rank IC (0.18-0.29) is inflated by the paired long/short rows
   and should not be quoted as skill. The side-matched control strips the side effect and a gap remains, but part of it
   is label-free: the shuffled-label model came close to the VOID line (p 0.063), consistent with cost-in-R selection
   (high-ATR rows are cheaper in R) rather than timing.
3. **Positioning and calendar columns are used but do not create edge.** COT and GLD columns take 4 of the top 10
   gain slots in M1. They change weekly, so with 8 years they can act as regime labels; the forward leg's negative net
   is what that would look like. This matches R-19 (no gross signal as rules).
4. **M2 is a cost problem, not a model problem.** The I2B rule's structural stop has a median of $0.61-0.94, so the
   charged round trip, overshoot and swap cost 0.5-0.75 R a trade. No filter can recover that: the best decile still
   loses 0.48 R.

## Consequences

- ML-M1 and ML-M2: **failed** (`hypotheses.md` A24). **R-20 (extends R-07):** meta-label filters over the I0 random
  schedule (both sides) or I2B box-break candidates with this point-in-time set (price, volatility, session,
  COT/GLD positioning, tier-1 calendar) are retired. Reopening needs a new information source (order flow, options
  surface, author calls), not a new algorithm or a new subset of these columns.
- 2018+ stays sealed. Nothing goes to demo.
- **K +2** (one per model). Ledger: 3,014 (A22 growth row) + 2 = 3,016 for merged lanes; lanes still in flight add
  their own.

## Proposal for the research director (not run; needs a slot and its own pre-registration)

**ML-M1c (prior 10-15%, K +1, about 2 agent-hours):** the M1 model and features as frozen here, on a geometry where
costs are at most 0.03 R: 1h decisions, 1.5 x ATR14(1h) stop, 2 R target, 24-bar hold, swap charged. Reason: M1's
measured +0.10 R over matched random selection is larger than that cost but smaller than I0's 0.13 R. **This idea comes
from this result, so it is data-snooped.** It must therefore pass the same bar on 2010-2017 *and* a one-look confirm
on 2018-01..2025-09 before any demo arm. Risk to state up front: 2018-2025 is a strong bull market, and a selector that
is 89% short because of the swap will probably fail there. It should be run only if the director judges a structural
(swap/cost) selector worth a slot.

**Stop doing:** meta-labelling over tight structural-stop generators (S-, I2-, box families). Their costs in R
(0.3-0.8 R) are larger than any ranking gain we have measured (0.10 R).

## Reproduce

`.venv/bin/python scripts/research/meta_label_screen.py --raw raw --positioning raw/positioning/positioning.parquet
--cal <calendar-v1 us_calendar.parquet> --out ~/tmp/meta_label/result.json --workers 4` (`--dry`: counts, coverage and
the lookahead test only). Seeds: model 20261101, bootstrap 20261101, control 20261102, shuffle 20261103 + year.
