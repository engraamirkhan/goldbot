# Pre-registration: ML-M1c, the M1 meta-label model on 1h bars (ML researcher, 2026-11-01)

Slot approved by the research director on 2026-11-01, under the owner's standing directive to find strategies.
Frozen by commit **before** any 1h label, fit or outcome. **K +1.** 0 registry trials. Parent:
`prereg-ml-meta-label-2026-11-01.md` (M1). Everything not restated here is M1's frozen rule, unchanged.
Result goes to `meta-label-screen-2026-11.md` (section M1c).

**Disclosed:** this idea comes from M1's result. M1 beat random selection matched on year and side by about 0.10 R,
but lost about 0.13 R a trade to costs. It is therefore data-snooped, and that is why it needs a 2018+ confirm.
**Prior: 10-15%.** M1's top decile was 89% short, so the most likely failure is the new side leg (g) below, or the
2018-2025 bull market in the confirm.

## 1. What changes from M1 (nothing else changes)

| Item | M1 | M1c |
|---|---|---|
| Decision bars | 15m | **1h** mid bars (`resample_bars(b1, "1h")`), decision at the bar close |
| Generator | I0 `random_bracket` | the same `arm_rules.random_bracket` code on a copy of the I0 arm with `timeframe: 1h` and `hold_bars: 24` (closes at :00, 07:00-19:00 UTC, Mon-Fri, common filters). Every decision is taken **both ways** |
| Barriers | 1.5 x ATR14(15m), 2 R, 96 x 15m | **1.5 x ATR14(1h)** stop, 2 R target, **24 x 1h** time limit (the same 24 hours) |
| Features | 27 decision (15m) + h1/h4/d1 context + 5 positioning + 3 calendar + side | the same 27 names computed on **1h** bars. The context `h1_adx14` would duplicate the decision bar's own `adx14`, so it is replaced by **`h4_adx14`** (pre-registered substitute). Context: `h4_adx14`, `h4_dist_ema50_atr`, `d1_slope_ema20`, `d1_atr_ratio_14_100`. Still 40 columns |

The following are unchanged: the label (net R, `cor-2026-10-31.2` ICM charged cells, overshoot on stops, long swap
$0.60/oz/night with Wednesday triple), the 26 h wall-clock drop, the LightGBM regressor and its hyperparameters and
seed, the purged and embargoed year folds, the anchored forward leg 2012-2017, the per-year top decile, all seeds,
the lookahead test, the shuffled-label validity gate, and up to 4 workers.

## 2. Screen bar (2010-2017, one look; all legs must hold)

Legs (a)-(e) are M1's, with two changes:
- **(c)** is tested at p <= **0.05**. This is one model in its own slot, so there is no Bonferroni-2.
- **(g) is new:** in the top decile, the long net mean R >= 0 **and** the short net mean R >= 0, each over >= 30 trades. A
  side with fewer than 30 trades fails the leg. This stops a short-only selector (the swap-avoidance artefact) from
  passing.

The validity gate, VOID if the shuffled-label model beats the control at p <= 0.05, and the lookahead test are as in M1.

## 3. Confirm (run once, and only if every screen leg passes)

- **Window:** 2018-01-01..2025-09-30 (the holdout 2025-10..2026-09 stays sealed). One model is fitted on **all
  2010-2017 rows** whose label exits before 2018-01-01 - 26 h. It scores 2018-2025-09 strictly forward, with no refit.
- **Selection:** the top decile per calendar year of the confirm (as in the screen).
- **PASS only if all hold:**
  - net mean 90% lower bound > 0 (week-block bootstrap);
  - the top decile beats the year- and side-matched random selection at p <= 0.05;
  - >= 5 of 8 years with net > 0 (2025 counts if it has >= 20 trades);
  - leg (g) on the confirm.
- **FAIL:** retired, final. M1c joins R-20.
- **PASS:** the result goes to the director for a qualification decision (a demo arm through ADR 0061). Sizing and
  live trading stay owner decisions.
- **Code:** the confirm code is written and committed only if the screen passes, and before the confirm runs. It
  only adds the window switch.

## 4. Reading

- **Screen FAIL:** M1c is retired with R-20. No further geometry variants of M1 (other bars, stops, holds, or side
  restrictions) without a new information source.
- **Screen PASS, then confirm FAIL:** the same.

## Dry-run facts (2026-11-01, before any label statistic or fit; no rule change)

The `--models M1c --dry` run reports counts, coverage and the lookahead test only.
- **Candidates:** 41,900 (20,950 decisions x 2 sides; 5,110-5,408 a year).
- **Median risk:** $4.87/oz. The charged round trip is therefore about 0.04 R, and the long swap about 0.12 R a night.
- **Lookahead test:** 12 cuts x 50 rows, 0 mismatches.
- **Missing feature values:** at most 4.1% (`d1_atr_ratio_14_100`).
