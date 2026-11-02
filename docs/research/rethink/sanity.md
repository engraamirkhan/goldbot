# Rethink lane `sanity`: is the backtest machinery right? (2026-11-02)

Question: do zero qualifiers out of about 3,330 trials come from broken engines? We tested two engines against known
truths:
- `scripts/sprint200/trend_screen.py` (`walk`, `price`, `simulate`, `summarize`);
- the arms harness `scripts/run_arm_backtest.py` (`triple_barrier_next_open_1m`, `_finish`).

`goldbot/research/sprint3000` is not merged and was not tested.

Rules for these runs:
- Inputs were Dukascopy 1m up to 2025-09-30 (the holdout was never read), seeded synthetic series, and broker ticks
  from the brain (read-only).
- Only summary statistics were kept.
- At most one or two processes ran at a time.

**Verdict: the price mechanics are correct. The swap was not, and the charged costs are about 2.4 times what demo
fills cost.** The engines book prices exactly, recover a planted edge to three decimals, and show no lookahead on a
random walk. They also agree with each other trade by trade.

Two cost terms are mis-scaled, both against the strategies:
- **Swap:** fixed dollars. It is now a rate (ADR 0070).
- **Round trip plus overshoot:** charged at 2.4 times the demo cost we realise. This is a calibration question for the
  quant reviewer, not a code bug.

Neither one, corrected, flips a sprint-200 trend verdict.

## Pass/fail

| check | result | numbers |
|---|---|---|
| (a) buy-and-hold vs the price change | PASS prices / FAIL swap (fixed) | 2010-2017: walk +206.519 = truth +206.519 (diff 0.0); harness +205.09 (enters at the next session open). 2018-2025-09: +2,559.997 both. Swap charged -$1,751/oz on +$206 (16.2%/yr of the mean price; today's ICM rate is 4.9%/yr). Fixed: rate on the entry price, about -$607 |
| (b) planted +0.2 R (time exit, R = $20) | PASS | walk gross +0.2007 R (se 0.0014), net +0.1884 = gross - rt/R 0.0123 exactly. Harness gross +0.2007, net(cor) +0.1884. Same rule with alternating sides: +0.0010 (se 0.0024) |
| (c) random walk (8 years, martingale) | PASS | All 34 trend rules: 0 pass. Gross t from -2.00 to +1.58, mean +0.11 (n 32 with trades). Net = gross - cost. Harness W0 gross -0.005 R (t -0.11), I0 +0.011 (t 1.08). Planted rule on the walk: +0.0020 (se 0.0015) |
| (d) TSMOM (MOP 2012: sign of 12m/1m return, monthly, 40% vol target) | PASS (sign and size) | 12m: Sharpe 0.17 in 2010-2017, 0.42 in 2018-2025-09. 1m: -0.18 and 0.57. Positive and modest, in line with commodity trend after 2010 (MOP report gold about 0.3-0.5 over 1985-2009). Engine monthly gross equals the pandas calc (annual 0.0194 vs 0.0197). Swap drag on the shipped spec 7.4-8.1%/yr of notional |
| (e) unit cases | PASS after fix | Shared minute takes the stop (both engines, both sides). Bid/ask: buy at the ask open, stop on the bid low (shorts mirror). Swap sign: a long pays, the shipped short is 0. Gap through the stop: walk fills at the open; the **harness filled at the stop (fixed)**. ms-unit Dukascopy files load as epoch ns |
| (f) replica parity, 1,371 closed ICM demo trades, 40 arms, 10-26..10-30 | PASS mechanics / costs conservative | Exit kind agrees on 95.6% (stop 587/618, target 523/540, time 200/213). Exit time median 26 s apart (p90 72 s). Mean R: simulated -0.002, real +0.052, real - sim **+0.054 R (se 0.010)** = $0.184/oz (se 0.036) per trade against a charged $0.313. Realised all-in cost on same-kind exits: stop 0.109, target 0.046, time 0.093 $/oz, against charges of 0.405 / 0.238 / 0.244. Swap -0.0033 real vs -0.0039 sim |

The two engines on the same real minutes (W0 and I0, 2010-2017, 20,744 trades):
- 20,534 trades are identical.
- The other 210 are gap stops, where the harness was optimistic by $1.10-1.25/oz.
- Harness swap timing (exit-bar close vs exit minute) differs by -0.0004 R.

## Bugs (line numbers on origin/main 4eb18ece)

| # | where | what | impact on past verdicts |
|---|---|---|---|
| 1 HIGH | `goldbot/labels/triple_barrier.py:44` (`SwapSpec.usd_per_oz`), used by `trend_screen.py:935`, `triple_barrier._finish`, `run_arm_backtest.py:118,169`, and `macro_screen.py:560`, `meanrev_screen.py:906`, `patterns_screen.py:447` | Swap is charged in fixed dollars at all price levels. That is 2.5-3.3 times today's rate on 2010-2021 trades | Trend lane re-read (explore / confirm), scaled: net rises up to +0.08 R on multi-day rules, **0 verdict flips** (03 confirm +0.070 to +0.096, t 0.99 to 1.33, still FAIL). Fixed in the trend screen and the harness. The other three screens are pending (delegated) |
| 2 LOW | `goldbot/labels/triple_barrier.py` `triple_barrier_next_open_1m` (via `triple_barrier` stop fill) | A minute that opens through the stop was filled at the stop | Optimistic: +0.0012 R (W0) and +0.0048 R (I0). It could only have helped arms pass. Fixed |
| 3 LOW | `trend_screen.py:932` `price` | Off-session entries are charged the Asia cell (0.272 / 0.136). The harness charges the dearest (0.272 / 0.196) | Up to $0.06/oz less on off-session stop exits in the trend lane. Not fixed (one-line follow-up) |
| 4 LOW | `triple_barrier._finish` | Swap runs to the close of the exit's decision bar, not the exit minute | -0.0004 R (W0), -0.00005 R (I0). Not fixed |

## Findings that are not bugs (ranked)

1. **Charged costs are about 2.4 times realised demo costs.** The $0.313/oz charged per trade includes:
   - the round trip upper 90% bound;
   - the overshoot floor;
   - the Asia cell.

   Realised demo cost, commission included, is about $0.13/oz. On the $3-stop scalps that is +0.05-0.08 R per trade,
   enough to move a 1m/5m rule from a negative to a positive mean. Caveats:
   - Demo fills are a lower bound on live.
   - The sample is 5 days.

   Recommendation for the quant reviewer: price screens from a replica of real against simulated trades, not from
   component upper bounds stacked together. Keep the stress line.
2. **Swap reference: the broker pays shorts +36/lot today, and research credits 0.** This is ADR 0017 policy, recorded
   in ADR 0070.
3. **The DSR uses K = 3,233 independent trials with var(SR) = 1/n**, so confirm needs an effective t of about 3.5. The
   trials are heavily correlated (one family across timeframes counts many times). The effective K is a
   research-director decision. It is a structural reason for zero qualifiers, not an engine fault.
4. **Random controls on real 2010-2017 data lose gross:**
   - I0: -0.023 R, t -2.3;
   - W0: -0.112 R including swap, t -2.7.

   The same arms on a martingale give about 0. So these losses come from gold's path (stop-first brackets against
   intraday reversion) and the old swap, not from the engine.

Scripts and logs (not committed) are in the session scratchpad: `sanity_*.py`, `synth.py`, `swap_impact.py`, and
`mutate.py`. The mutation check killed 7 of 7 mutants.
