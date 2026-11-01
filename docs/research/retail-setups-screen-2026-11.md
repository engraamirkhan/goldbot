# Retail gold setups on 2010-2017: 0 of 3 pass (strategy researcher, 2026-11-01)

Owner 2026-11-01: "find strategies, setups, signals to add more". Brief: the setups gold retail traders use that the
repo has not screened (ICT/SMC, supply/demand, round numbers, VWAP bands, divergences, the owner's box + MFI), each
defined mechanically, at most 6 rules, matched random control, one look each on 2010-2017, pass bar per
`growth-sprint-2026-11-01.md` section 2. Pre-registration `prereg-2026-11-01-retail-setups.md` (commit `9fc69477`),
code `docs/research/candidates/retail/retail_screen.py` (commit `3779c53b`, walk tests
`tests/research/test_retail_screen_walks.py`), both committed before the run. One run, 3 worker processes (one per
rule), 2.6 s. Inputs local only: `raw/xauusd_1m_dukascopy_2010..2017.parquet`; cost of record `cor-2026-10-31.2`
(ICM, x1.5). 2018+ and the holdout were not read (the loader asserts it).

## 1. Coverage: 7 of the 9 requested setups were already closed

Not re-run (no new mechanism or data; details and sources in the prereg, section 0): FVG fills/magnet (slice E, E-2),
order-block retest (E-1: +0.04..+0.08 R over random controls, CI includes 0), Asia high/low sweep into London (#88,
R-14; H-76), round-number magnet (G-7: touch rates equal to random levels), VWAP-band reversion (R-01; session VWAP
fades gross -0.05..-0.53 R), RSI/MFI divergence (signs flip across 15m/1h/4h; H-40 AUC 0.503), the owner's box break +
close + MFI (#89, R-15; H-80 R2). Screened here: the two requested setups never measured (silver bullet, supply/demand
zones) and one I added, the inverse FVG (the most common 2024-26 retail ICT entry, in no earlier slice).

## 2. Result (frozen rule, one look)

R = net USD/oz over the stop distance. Twin = 4 random-time, fair-coin-side limit orders per setup with the same stop
distance, limit offset, target and lives. Pass = n >= 200, net mean 90% LB > 0, real minus twin p x 3 < 0.10, >= 5/8
years positive.

| Rule | Setups / gated / busy / unfilled | n | gross R (t approx) | net R (90% LB) | median cost R | twin net R | real - twin per day (Bonf-3 p) | yrs + | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| RS-1 silver bullet NY AM, 5m | 582 / 42 / 0 / 194 | 346 | +0.083 (1.3) | **-0.052** (-0.153) | 0.12 | -0.197 | +0.085 (0.068) | 4/8 | FAIL |
| RS-2 supply/demand retest, 1h | 592 / 160 / 8 / 101 | 323 | -0.090 (-1.1) | **-0.284** (-0.410) | 0.19 | -0.366 | +0.071 (0.49) | 0/8 | FAIL |
| RS-3 inverse FVG, 15m | 8,287 / 191 / 3,807 / 725 | 3,564 | +0.012 (0.6) | **-0.099** (-0.132) | 0.09 | -0.135 | +0.065 (0.15) | 0/8 | FAIL |

Exits: RS-1 stop 40% / target 17% / time 43%; RS-2 69% / 30% / 1%; RS-3 47% / 20% / 33%. Median stop $2.60 / $1.99 /
$3.90 (2010-2017 prices). Side split (reported, not a verdict): RS-1 long +0.10 R (n 185), short -0.22 R (n 161);
RS-2 long -0.06, short -0.45; RS-3 long -0.14, short -0.06.

## 3. Reading

1. **No rule has an edge before costs.** Gross t is 1.3, -1.1 and 0.6. From the reported sd, a zero-cost 90% lower
   bound would still be below zero for all three (RS-1 about -0.02 R, RS-3 about -0.02 R). So the FAIL does not
   depend on charging 2026 USD costs on 2010-2017 prices (cost is 0.09-0.19 R here, several times what it is at today's
   ATR); a FAIL by cost alone would have needed a gross LB above zero.
2. **"Beats the twin" is the twin losing, not the setup winning.** All three real-minus-twin differences are
   positive because a limit order placed at a random time on a random side is adversely selected: it fills when price
   runs against it (twin gross -0.06 / -0.17 / -0.01 R). RS-1's +0.085 R per day clears Bonferroni-3 (p 0.068) on that
   leg only. Against a zero-edge benchmark the rule is still flat. This is the same pattern as slice E's E-1 (order
   block gross +0.11 R, nothing over control): ICT levels are about as good as any pull-back level.
3. **RS-1's long side (+0.10 R net) is a post-hoc split**, not a lead. Testing it would be a new look at a selected
   subset, and 2011/2014/2016/2017 are negative for the whole rule. Per the prereg, no neighbour variants (London or PM
   silver bullet, other gap floors, other R multiples) are run.
4. **RS-3 shows why "busy" matters**: 8,287 inverse-gap setups in 8 years, 46% of them skipped because a trade was
   already open. Inverse gaps are everywhere on 15m, which is why they cannot carry information on their own.

## 4. Decisions

- **Retire RS-1, RS-2, RS-3 as specified** (R-RS in `hypotheses.md`, section A26). Do not re-test on XAUUSD without
  new evidence (a mechanism or data source not used here, for example order-book or futures volume at the level).
- With this screen, the retail SMC / price-action list is closed as stand-alone rules: order blocks, FVG (entry,
  magnet, inverse), silver bullet, supply/demand, sweeps, round numbers, VWAP bands, divergences, box + MFI, ORB,
  Fibonacci, pivots. Their point-in-time flags stay available as model features (counted where used), not as rules.
- **Not recommended:** a demo arm for any of the three (owner rule 10-30: no strategy on demo without a history pass).
- **What would change my mind:** a data source that measures the claimed mechanism directly (resting liquidity at
  the level: COMEX GC depth or tick-volume at price), not another definition of the same chart pattern.

## 5. Owner-only flags

None raised by this screen: no confirm is earned, so no registry slot is requested; no cap, size or instrument
changes.

## 6. Multiple testing

K +3 (one look per rule; the 7 already-closed setups were not re-run, +0). Program K on the ledger 2026-11-01:
3,020 (after A24 and A25) -> **3,023**. 0 registry trials, 0 budget slots; `trial_budget_overrides`
unchanged. Holdout sealed.

## Reproduce

`.venv/bin/python docs/research/candidates/retail/retail_screen.py --raw raw --workers 3`. Outputs to
`~/tmp/retail_screens/` (results JSON, per-trade Parquet); nothing is committed. Bootstrap seed 20261101; twins seeded
by sha256(rule, setup time, i).
