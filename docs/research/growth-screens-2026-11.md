# Growth screens C1, C2, C4 on 2010-2017: 0 of 5 pass (exits researcher, 2026-11-01)

Owner goal 2026-11-01: double capital weekly. Candidates and pass bar: `growth-sprint-2026-11-01.md` section 2
(research director, PR #251). Operational details frozen by commit `fd02d0de` before any outcome was computed:
`prereg-growth-2026-11-01.md`. Code: `candidates/growth/growth_screen.py`; walk tests
`tests/research/test_growth_screen_walks.py`. Inputs local only: `raw/xauusd_1m_dukascopy_2010..2017.parquet`,
calendar-v1 `us_calendar.parquet`; cost of record `cor-2026-10-31.2` (ICM, x1.5). Holdout and 2018+ untouched (the
loader asserts it). One look each; a full run takes about 3 s, so no parallel workers were needed.

The owner's own three rule texts (0.5 x ATR15 pyramid with a 2 x ATR15 trail, NR7/inside bar on 1h/4h with a 3 x ATR
trail, +-1 x ATR15 straddle with a 2 x ATR15 trail) were replaced by the director's frozen C1/C2/C4 before any code ran.
They were never computed and add nothing to K.

## Result (frozen rule, conservative intrabar order)

R = net USD/oz over one unit's initial stop. Pass = net mean 90% LB > 0, AND beats a 4-twin random-entry control with
identical mechanics at Bonferroni-7 p < 0.10, AND >= 5/8 years positive (C1 also: whipsaw <= 45%). Weekly ledger:
every calendar week 2010-2017 (417), compounded trade by trade, f = risk per 1 R.

| Variant | n | trades/wk | gross R | net mean R (90% LB) | skew | P(R >= +5) | top-10% share of R | vs twin (Bonf-7 p) | yrs + | f=5%: P(x2) / P(<=x0.5) / median | f=10% | f=20% | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| C2a pyramid, Asia range -> London | 1,643 | 3.9 | -0.01 | **-0.36** (-0.57) | 8.5 | 4.5% | n/a (total -593 R; top decile +1,422 R) | -0.13 (1.00) | 1/8 | 3.8% / 0.0% / 0.78 | 7.9% / 15.8% / 0.59 | 7.7% / 76.3% / 0.32 | FAIL |
| C2b pyramid, London range -> NY | 1,771 | 4.2 | +0.22 | **-0.03** (-0.19) | 6.0 | 5.4% | n/a (total -45 R; top decile +1,680 R) | +0.06 (1.00) | 3/8 | 3.4% / 0.2% / 0.83 | 6.7% / 4.6% / 0.69 | 9.1% / 57.1% / 0.44 | FAIL |
| C4 NR7 break, 4 R target | 290 | 0.7 | +0.20 | **+0.05** (-0.12) | 1.2 | 0.0% | 7.7x (total +15 R) | +0.16 (0.41) | 5/8 | 0 / 0 / 1.00 | 0 / 0 / 1.00 | 0.5% / 0.5% / 1.00 | FAIL |
| C1a news straddle k 0.5 | 256 | 0.6 | +0.65 | **+0.09** (-0.14) | 2.2 | 5.1% | 6.5x (total +24 R) | +0.21 (0.60) | 3/8 | 0 / 0 / 1.00 | 0.5% / 0.2% / 1.00 | 3.1% / 1.9% / 1.00 | FAIL (whipsaw 64% > 45%: falsified) |
| C1b news straddle k 1.0 | 251 | 0.6 | -0.14 | **-0.42** (-0.50) | 2.1 | 0.0% | n/a (total -105 R) | -0.37 (1.00) | 1/8 | 0 / 0 / 1.00 | 0 / 0 / 1.00 | 0 / 0.2% / 1.00 | FAIL |

Geometric-mean weekly multiple at f = 5 / 10 / 20%: C2a 0.868 / 0.703 / 0.404; C2b 0.936 / 0.772 / 0.531; C4 0.999 /
0.993 / 0.969; C1a 0.999 / 0.991 / 0.903; C1b 0.987 / 0.972 / 0.940. Every one is below 1: bet harder and the
account shrinks faster.

## Reading

1. **The pyramids are the only rules that ever double a week, and they do it the way a lottery does.** C2 puts
   3-5% of weeks at x2 or more at f = 5%, with skew 6-8 and 4.5-5.4% of trades at +5 R or more. That is the convexity
   the owner asked for. But the median trade is -1.2 R (78-94% end at a stop), the net mean is negative, and the median
   week is x0.6-0.8 at f = 5-10%. At f = 20%, 57-76% of weeks end at or below x0.5. This is section 1's martingale
   bound in data: convexity changed the shape, not the mean.
2. **C2b has gross edge (+0.22 R) that costs eat.** Units average 1.8 per trade, each paying a 1.5 x round trip
   plus stop overshoot (net minus gross is 0.25 R). It does not beat its
   random twin (+0.06 R, p 0.28 raw), so the gross figure is the pyramid's payoff shape on a drifting market, not entry
   skill. C2a has neither.
3. **C4 is the nearest miss and still not close.** +0.05 R net, +0.16 R over the twin (raw p 0.059, Bonferroni-7
   0.41), 5/8 years, but the 90% LB is -0.12. With a 4 R target and a 1 R stop it has no +5 R tail by design, 0.7
   trades a week, and its best week at f = 20% is x3.2 once in eight years.
4. **C1's verdict depends on the 1-minute bar, so I bracketed it (disclosed below).** 77-92% of fills land in the
   release minutes, and 53% of C1b's trades are stopped in their own fill minute under the frozen conservative order.
   1m bars cannot say whether the minute's low came before or after the fill.

## Intrabar bracket (disclosed after the frozen read; not a verdict; K +5)

Same code with `--optimistic`: in a minute with both, the C2 stop is checked before the adds; C1 and C4 stops are not
checked in the fill minute. This bounds the 1m-resolution error from the other side. C2a, C2b and C4 barely move
(C2b net -0.02, 4/8 years). **C1b flips to the bar:** net +0.14 R (90% LB +0.011), +0.18 R over the twin (Bonferroni-7 p
0.083), 6/8 years, whipsaw 40%. C1a stays FAIL (whipsaw 64%). Even this optimistic C1b compounds at x1.003 a week at
f = 5% and x0.999 at 20% (0.6 trades a week): no doubling route even if it is real.

The truth for C1b lies between -0.42 and +0.14 R. That is a measurement gap, not a tuning opportunity: it is closed only
by tick data in the release minutes, which we do not hold locally for 2010-2017.

## Decisions and proposals (exits researcher)

- **Retire as specified:** C2a, C2b, C4, C1a. Do not re-test pyramided session-range breakouts or NR7 4 R targets on
  XAUUSD without new evidence (an entry that beats its twin). GS1 on demo (C2 pair) now runs as "unqualified, screen
  FAIL", as section 3 of the sprint doc pre-committed.
- **C1b: one pre-registered tick-resolution re-measurement, if the director gives it a slot** (K +1): the same frozen
  rule on Dukascopy ticks for the about 250 release windows only (T-2..T+240 min), data pulled by the existing
  workflow; pass on the same bar. This removes the intrabar ambiguity instead of choosing the side that passes. Expected
  information is high per unit of cost (one rule, one look); expected profit is small, since 0.6 trades a week cannot
  move weekly growth.
- **Stop doing:** convexity-for-growth screens without an entry that beats its random twin first. The pyramid adds
  amplify whatever the entry has; here the entry has nothing.

## Owner-only flags

Risk of 5-20% per trade exceeds the 1% RiskGate design limit and the £50 daily loss limit on £1,000. The ledgers above
are virtual. No rule here justifies a cap change, and sizing stays an owner decision.

## Multiple testing

Director's sprint K +7 (3,002 -> 3,009) covers C1 x2, C2 x2, C4 x1 (this lane) and C3 x2 (another lane). The disclosed
intrabar bracket adds **K +5** (5 more looks at the same rules): program K **3,014** once C3 reports.

## Reproduce

`.venv/bin/python docs/research/candidates/growth/growth_screen.py --raw raw --cal ~/tmp/ts-data/cal/us_calendar.parquet`
(add `--optimistic` for the bracket). Outputs to `~/tmp/growth_screens/` (results JSON, per-trade Parquet); nothing is
committed. Bootstrap seed 20261101, twins seeded by sha256(variant, day, i); two frozen runs gave identical results.
