# 0070: Research charges swap as a rate on each trade's notional

Status: proposed (principal test & quality engineer, rethink lane `sanity`, 2026-11-02; for quant-reviewer ruling)

## Context

Swap is overnight financing: a rate on the position's notional. The research engines charged it in fixed dollars.
`SwapSpec.shipped()` charges -60 USD per lot (-0.60 USD/oz) per night for longs at every price level, in both
`scripts/sprint200/trend_screen.py` and the arms harness (`triple_barrier._finish`, `scripts/run_arm_backtest.py`).

The rethink sanity lane ran buy-and-hold through both engines. Prices were exact to 1e-9, but the swap was out of scale:

| window | buy-and-hold gross | swap charged | swap as a share of the mean price per year |
|---|---|---|---|
| 2010-2017 | +$206.5/oz (+18.8%) | -$1,751/oz (2,919 nights) | 16.2% |
| 2018-2025-09 | +$2,560/oz (+196%) | -$1,697/oz (2,829 nights) | 12.2% |

ICM measured -52.2 USD/lot long and +36.0 short on 2026-11-02, with gold near $3,907. That is 4.9% a year. Because the
charge was fixed in dollars, every pre-2025 long paid 2.5 to 3.3 times today's rate in R terms.
- An H4 trend hold of about 10 nights in 2013 paid about 0.37 R of swap.
- At today's rate on its own price, the same hold pays about 0.13 R.

## Decision

1. **The rate model.**
   - `SwapSpec` gains `ref_price_usd`, the gold price the per-lot rates stand for.
   - `usd_per_oz_at(side, price)` charges the rate at the trade's entry price: `usd_per_oz x price / ref_price_usd`.
   - `SwapSpec.research_shipped()` is `shipped()` with `ref_price_usd = costs.swap_ref_price_usd`, which is 3,900.
     -60 at 3,900 is 5.6% a year, still above the measured 4.9%.
2. **The research engines charge it.** These two charge it on the entry price:
   - `trend_screen.price`;
   - the arms harness (`_finish`, `runner_labels`, `cut_labels`).
3. **Live paths are unchanged.**
   - A spec without a reference price behaves exactly as before.
   - `shipped()` keeps no reference price.
   - The RiskGate, the engine, cost imputation and cost probes charge today's dollars at today's price. That is the same
     rate.
4. **The short swap stays 0 in research.** The broker pays +36 per lot today. In 2010-2021, with near-zero rates, short
   swaps were paid. Zero sits between the two, and ADR 0017 never counts a credit.
5. **Gaps through the stop.** The arms harness (`triple_barrier_next_open_1m`) now fills a minute that opens through
   the stop at that open, as `trend_screen.walk` already did.
   - Before this, 199 of 19,724 I0 trades in 2010-2017 filled $1.10/oz better than the market allowed.
   - The average effect was +0.005 R.

## Consequences

- Multi-day longs in history pay today's financing rate on their own notional, not today's dollars.
- Re-read of the sprint-200 trend lane:
  - Net R rises by up to +0.08 R on holds of several days, for example 01 H4-DON30-TR, which goes from +0.047 to +0.126.
  - No verdict flips. Explore still passes only 03. In confirm, 03 rises from +0.070 to +0.096 at t 1.33, still a FAIL.
- The other three sprint-200 screens still charge `usd_per_oz` and need the same change before any re-run:
  `macro_screen.py`, `meanrev_screen.py` and `patterns_screen.py`. The sprint-3000 engine must use
  `research_shipped()` and `usd_per_oz_at` from the start.
- The rate is today's. It does not model the rate regime: 2010-2015 carry was nearer 2-3% a year than 5.6%. This stays
  conservative for longs.
- What would change this:
  - A measured historical swap series, such as broker archives or a fed funds plus markup model with a quant ruling.
  - A different reference after the broker's terms move. Update `costs.swap_ref_price_usd` together with the -60 prior.
