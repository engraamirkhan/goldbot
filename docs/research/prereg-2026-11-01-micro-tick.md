# Pre-registration: tick-proxy microstructure screen VAC / TIM / SRR (microstructure researcher, 2026-11-01)

Frozen by commit before any 2010-2017 outcome is computed. Pass bar: `growth-sprint-2026-11-01.md` section 2 (research
director), applied here over 3 variants. Results: `micro-tick-screen-2026-11.md`. Code: `scripts/research/micro_tick_screen.py`
(walk/cost/signal tests `tests/research/test_micro_tick_screen.py`). Seen before freezing: **signal counts only**
(`--count-only`, no price path after a signal, no R); VAC's blowout threshold was moved from spread_max >= 3 x median
(18,276 signals, i.e. ordinary tick noise) to spread_mean >= 1.5 x median (539) on counts alone.

## Data, honestly named

Raw Dukascopy ticks for 2010-2017 are not on this machine. The tick-level information held locally is in the 1m
bid/ask bars `raw/xauusd_1m_dukascopy_2010..2017.parquet`: per-minute `tick_count` (Dukascopy's fractional volume
field, used only as a relative activity proxy against its own trailing median), `spread_mean`, `spread_max`, and
bid/ask OHLC. "Tick imbalance" below is therefore a tick-volume-weighted 1m sign imbalance, not a Lee-Ready trade sign.
That limits what a FAIL can retire (reading rule, last paragraph).

## Common (all three rules)

| Item | Frozen value |
|---|---|
| Window | 2010-01-01..2017-12-31; the loader asserts no 2018+ bar. Confirm 2018-01..2025-09 and holdout 2025-10..2026-09 untouched |
| Prices | mid = (bid + ask) / 2 per OHLC field. Trailing features use bars t-60..t-1 only: ATR1m = mean(high - low), median spread_mean, median tick_count |
| Decision / latency | on the completed signal bar t; fill at the open of bar t+1 (one bar of latency); skipped if bar t+1 is > 5 min later or outside 07:00-20:30 UTC |
| Exit | stop at entry -+ R (a gap fills at the minute's open, worse); target at entry +- m R (a gap fills at the level only); a minute holding both is a stop; else the last close before min(fill + hold, 21:00 UTC) |
| Cost | `config/cost_of_record.yaml` **cor-2026-10-31.2**, ICM cell of the fill session (asia < 07:00, london 07:00-12:30, newyork >= 12:30 UTC): 1.5 x round_trip_usd, + 1.5 x overshoot_usd on a stop exit, + 1.5 x 0.5 x cell spread_usd x max(0, Dukascopy spread / its 60-min median - 1) on each leg (entry: the fill bar's open spread; exit: the exit bar's mean spread). A blown-out spread costs proportionally more |
| R | net USD/oz over the trade's stop distance |
| Random twin | 4 per real trade: uniform random 1m bar 07:00..20:30 on the same UTC day, fair-coin side, the real trade's R in USD, same target multiple, hold and cost model; seeded sha256(rule, trade id, j) |
| Pass (each rule) | (a) net mean R day-block bootstrap (10,000, seed 20261101) 90% LB > 0 **and** its one-sided p Holm-adjusted over 3 < 0.10; (b) real minus mean-twin R, day-block bootstrap, Holm-over-3 p < 0.10; (c) >= 5 of 8 calendar years net mean R > 0. All three, else FAIL |
| Compute | 4 worker processes (one per year), one run, one look |

## The three rules

| | VAC liquidity-vacuum reversal | TIM tick-imbalance continuation | SRR stop-run reversal |
|---|---|---|---|
| Signal on bar t | spread_mean_t >= 1.5 x median spread; abs(c_t - c_{t-3}) >= 3 x ATR1m | I5 = sum(v_i sign(c_i - o_i)) / sum(v_i) over t-4..t, abs(I5) >= 0.6; sum(v) >= 1.5 x 5 x median(v over t-64..t-5); sign(c_t - o_{t-4}) = sign(I5) | prior trading day (22:00-22:00 UTC) high/low; first bar in 07:00-19:54 whose high (low) pierces it; that bar's v >= 2 x median v (stop-trigger burst; else that side is done for the day); a close back inside within 5 minutes = signal bar |
| Side | against the 3-minute move | sign(I5) | against the pierce |
| R | max(distance to the move's 3-bar extreme + 0.5 ATR1m, ATR1m) | 2 x ATR1m | max(distance to the sweep extreme + 0.5 ATR1m, ATR1m) |
| Target / hold | 1.5 R / 15 min | none / 5 min | 2 R / 60 min |
| Spacing | next signal >= 15 bars later | >= 6 bars later (no overlap) | one per side per day |
| Signals (counts seen) | 539 (2010 27 .. 2013 162) | 28,054 | 382 |

## Economic reason and literature (none net of costs; all single-market, none replicated on gold)

- **VAC.** When liquidity providers withdraw (spread widens) a fast move overshoots and reverts as they return:
  Grossman and Miller 1988, *J. Finance* 43(3), theory; Nagel 2012, *RFS* 25(7), US equities 1998-2010, daily
  reversal returns rise when liquidity evaporates, gross *(unverified)*; Hendershott and Menkveld 2014, *JFE* 114(3),
  NYSE 1994-2004, inventory price pressure reverts within days, gross *(unverified)*. New here: spread state as the
  trigger. No retired family conditioned on spread (Z2 run-exhaustion and Z8 absorption used volume only, H-81).
- **TIM.** Order-flow imbalance persists over minutes: Chordia and Subrahmanyam 2004, *JFE* 72(3), NYSE 1988-1998,
  daily, gross *(unverified)*; Cont, Kukanov and Stoikov 2014, *J. Financial Econometrics* 12(1), US equities 2010,
  mostly contemporaneous impact, gross *(unverified)*. Neighbour Z1 tick-surge continuation (single bar) is bounded at
  gross +-0.011 R by H-82. New here: multi-minute weighted persistence, 5-min time exit.
- **SRR.** Stops cluster just beyond obvious extremes; their triggering is a short liquidity event. Osler 2003,
  *J. Finance* 58(5), and Osler 2005, *J. Int. Money and Finance* 24(2), NatWest FX order book 1999-2000, gross
  *(unverified)*: **stop cascades predict continuation, not reversal**. The reversal sign tested here is practitioner
  (SMC) folklore; Osler predicts a negative gross mean. Neighbour R-14 (H-48, 15m shallow sweep fade at prior-day /
  session extremes, gross -0.028 R, t -1.17 on 3,210) is retired; the new evidence offered is 1m resolution plus the
  tick-burst stop-trigger signature, which the brief asked to test.

## P4 screen expectation (written before outcomes)

Median ATR1m 07:00-21:00 is about $0.24-0.42 (2010-2017), so 1.5 x the ICM round trip ($0.25) is about 0.3-0.5 R for
TIM and 0.2-0.4 R for VAC/SRR before overshoot and spread surcharge. **Each rule needs a gross mean of roughly
+0.3-0.5 R to pass.** No 1m family in this program has shown more than +-0.02 R gross (H-81/H-82). Prior P(pass):
VAC about 5%, TIM under 3%, SRR under 3% (Osler). SRR's 382 signals (about 0.9 a week) are thin: it is scored, and a
pass would be read with that in mind.

## Reading rule (before data)

A rule passes only on (a), (b) and (c). A pass earns one confirm request to the research director (2018-2025-09,
program deflated bar t ~4.0 at registry K, DSR >= 0.95, >= 200 trades) and nothing else: no parameter is tuned on this
read, no demo arm. A FAIL retires the rule **as specified at 1m-bar resolution**: VAC spread-blowout fades, TIM 1-5
min weighted-imbalance continuation, and (with R-14) prior-day-extreme sweep fades at any bar resolution. Only true
tick or order-book data (Lee-Ready signs, depth) would reopen them, and that would be a new data source, not a
re-test. If SRR's gross mean is significantly negative (two-sided |t| >= 2), that is recorded as consistent with
Osler's continuation and nothing more: a continuation rule needs its own pre-registration and K.

## Multiple testing and trial cost

K before this lane: about 3,020 (brief; the growth-screens doc reads 3,014 once C3 reports, and parallel lanes may
add). **K += 3** (VAC, TIM, SRR), one look each; no registry trial (explore screen). The count-only threshold change
on VAC is a design step on counts, not an outcome look, and is disclosed above. Gates touched: none. Owner-only
items: none (no sizing, budget, instrument or live change).
