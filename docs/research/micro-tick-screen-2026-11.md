# Tick-proxy microstructure screen VAC / TIM / SRR on 2010-2017: 0 of 3 pass (microstructure researcher, 2026-11-01)

Pre-registration `prereg-2026-11-01-micro-tick.md`, frozen by commit `8472864a` before any outcome (only signal counts
were seen). Code `scripts/research/micro_tick_screen.py`, tests `tests/research/test_micro_tick_screen.py`. Inputs:
local `raw/xauusd_1m_dukascopy_2010..2017.parquet` (1m bid/ask with per-minute tick volume and mean/max spread; raw
ticks are not held locally). Cost of record `cor-2026-10-31.2`, ICM session cell x 1.5 plus a spread-state surcharge;
fill at the next 1m open. Holdout and 2018+ untouched (asserted). One run, 4 worker processes, about 10 s.

## Result (frozen rule, one look)

R = net USD/oz over the trade's stop. Pass = net mean 90% LB > 0 with Holm-3 p < 0.10, AND real minus 4 random twins
(same day, fair-coin side, same R, exits and costs) Holm-3 p < 0.10, AND >= 5/8 years positive.

| Rule | n | trades/wk | median R ($/oz) | gross mean R (t) | cost R | net mean R (90% LB) | twin net R | real - twin (90% LB; Holm p) | yrs + | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| VAC liquidity-vacuum fade | 539 | 1.3 | 0.78 | **+0.188 (3.55)** | 0.62 | -0.433 (-0.538) | -0.501 | +0.068 (-0.033; 0.41) | 1/8 | FAIL |
| TIM 5-min imbalance continuation | 28,054 | 67 | 0.75 | +0.005 (0.74) | 0.45 | -0.443 (-0.457) | -0.423 | -0.020 (-0.032; 1.00) | 0/8 | FAIL |
| SRR prior-day stop-run fade | 382 | 0.9 | 1.22 | +0.072 (1.06) | 0.36 | -0.288 (-0.409) | -0.369 | +0.081 (-0.054; 0.41) | 0/8 | FAIL |

Exit mix: VAC 50% stop / 45% target / 5% time; TIM 30% stop / 70% time; SRR 56% stop / 28% target / 16% time.

## Reading

1. **Costs decide all three, as the P4 expectation said.** At a 1m-scale stop ($0.75-1.22/oz) 1.5 x the ICM round
   trip is 0.36-0.62 R before any edge. No rule comes within a factor of three of it.
2. **VAC is the only real gross signal and it is not tradable.** +0.19 R gross (t 3.6) against a twin gross of +0.03 R:
   mid prices do revert after a minute of blown-out spread. That is about $0.15/oz, below even the unstressed ICM round
   trip ($0.16-0.20) before the blowout surcharge, and part of it is probably a quote artefact: a one-sided quote
   withdrawal moves the mid with no trade at that price, and the mid returns when the quote does. Only real ticks with
   executable bid/ask at the fill could separate the two. Net -0.43 R, 1/8 years.
3. **TIM has no continuation at all.** +0.005 R gross on 28,054 trades (t 0.7), below its own twin. Tick-weighted
   1m sign imbalance carries nothing over the next 5 minutes; this is H-82's +-0.011 R bound again from another entry.
4. **SRR: no reversal, and no Osler continuation either.** Gross +0.07 R (t 1.1), not significantly negative, so the
   pre-registered Osler note does not trigger. With R-14 this closes prior-day sweep fades at 15m and 1m.

## Decisions (microstructure researcher)

- **Retire as specified at 1m-bar resolution:** VAC, TIM, SRR. SRR joins R-14 (prior-day-extreme sweep fades at any
  bar resolution). Do not re-test spread-conditioned fades, weighted-imbalance continuation or burst-confirmed sweep
  fades on Dukascopy bars. Reopen only with a new data source (executable tick bid/ask or depth) and then only VAC,
  the one rule with gross signal, and only with a stop wide enough that cost is under 0.1 R.
- **Stop doing:** sub-15-minute rules at ATR1m-scale stops. The cost wall (0.35-0.6 R) is structural at our measured
  cost; every 1m/5m family to date (H-81, H-82, this screen) has gross within +-0.2 R.
- **What this says about scalping for growth:** the gross signals that exist at this horizon are an order of
  magnitude smaller than our round trip. A scalp needs either a cost cut of 3-5x (limit entries: H-30 is still the
  only route, pending the broker tick record) or a larger target, which makes it intraday.

## Multiple testing

K +3 (VAC, TIM, SRR), one look each, no registry trial. Program K 3,014 -> **3,017** (the ledger on main
read 3,014 with C3; the brief's base of about 3,020 double-counted). Later lanes add on top. No bracket, re-run or variant was computed.

## Owner-only flags

None. No sizing, budget, instrument or live change is implied.

## Reproduce

`uv run python scripts/research/micro_tick_screen.py --raw raw --out ~/tmp/micro_tick/result.json` (seed 20261101;
twins seeded by sha256(rule, trade id, j)). `--count-only` prints the pre-freeze signal counts. Output is not committed.
