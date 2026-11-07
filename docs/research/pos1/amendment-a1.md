# POS-1 amendment A1: universe re-stamp, costs, and the confirm-eligible table (frozen before any POS-1 read)

Research director, 2026-11-06. Line: **TREND-XASSET** (diversified trend / time-series momentum, 1d, vol-targeted).
This file amends `docs/research/pos1/prereg.md` (PR #285) and `docs/research/pos1/data-manifest.md`. It is committed
before any POS-1 strategy statistic exists: `state/position_reads.jsonl` holds no POS-1 claim at this commit, and the
only computations run so far are (a) a synthetic random-walk smoke test of the spec code and (b) the bar-date coverage
and Y_learn table below, which read dates, not prices.

## Why an amendment and not a new family

The owner's mandate of 2026-11-06 (diversified TSMOM / Donchian on 1d across the instruments, gold as a slice,
vol-targeted, at most 12 variants) is POS-1: specs 001/002 (TSMOM 12m and 1/3/12m), 004/027 (Donchian-Turtle 55/20),
025 (metals), 026/057 (diversified books), already pre-registered, quant-reviewed, with an engine built and never run.
Writing a second, overlapping family would double-count the same mechanism and inflate K. So the mandate is executed
as POS-1, with the smallest change the data forces.

## A1.1 Universe (data availability only; no outcome known)

The Dukascopy archive throttles this host (2026-11-06 19:5x UTC: ~30 s a request, 1,877 month-sides still missing
for USDCAD, USDCHF, NZDUSD and AUDUSD after 2014-05; ~8 h). Data availability is not an outcome, so the universe is
re-stamped to the markets on disk now:

| market | first bar | last bar | days | file sha256 (12) |
|---|---|---|---:|---|
| XAUUSD | 2003-05-05 | 2025-09-30 | 5,795 | 1m lane files (raw/xauusd_1m_dukascopy_*.parquet) |
| XAGUSD | 2003-05-05 | 2025-09-30 | 5,828 | aa5e8b8b4f0b |
| XPTUSD | 2021-11-01 | 2025-09-30 | 1,017 | 3423a76182d5 |
| WTI | 2011-09-23 | 2025-09-30 | 3,539 | 6dde553b01a8 |
| US500 | 2011-09-19 | 2025-09-30 | 3,478 | 5b3c44ebc41c |
| US100 | 2011-09-19 | 2025-09-30 | 3,492 | da7a82fbae51 |
| GER40 | 2013-09-30 | 2025-09-30 | 3,089 | 1387d8ab96ba |
| JP225 | 2011-09-19 | 2025-09-30 | 3,474 | 73ea5cefa15d |
| UK100 | 2011-09-19 | 2025-09-30 | 3,461 | 9369f0200acf |
| EURUSD | 2003-05-05 | 2025-09-30 | 5,847 | 19f100354221 |
| USDJPY | 2003-05-05 | 2025-09-30 | 5,847 | cc3c543d212a |
| GBPUSD | 2003-05-05 | 2025-09-30 | 5,847 | 1cedb79c102f |
| AUDUSD | 2003-08-04 | 2014-05-01 | 2,801 | ae3cf729671a (ends 2014-05: the rest is not fetched) |

- **U_A1** = these 13. USDCAD, USDCHF and NZDUSD are dropped (no file). AUDUSD trades until its last bar, where the
  engine closes it and pays the exit.
- **min_live = 6** (was 8 of 16). The prereg's 8 is "half the universe"; 6 is the count that keeps the 2004-2012
  book live (XAU, XAG, EUR, GBP, JPY, AUD), so LEARN-OLD and TEST-OLD exist at all. The cost is stated: before 2012-09
  the book is 2 metals + 4 FX, narrower than the prior assumes.
- Changed rules are new ids: **026 -> 026A, 027 -> 027A, 057 -> 057A** (same rules, U_A1, min_live 6). The original
  026/027/057 are **superseded unread** and are never run (running them later on the full U_16 would be a second look
  at 2003-2025 for the same mechanism). The other 13 ids are unchanged.
- **K.** POS-1 reads 16 ids. For any deflation the line counts K = 19 (16 + the 3 superseded ids), the conservative
  choice. Registry rows are written per id with the result.

## A1.2 Costs (C1 point costs per market, bps of price, round trip)

XAUUSD keeps the cost of record (icm/asia cell, 0.51 bp RT, W3 addendum ruling 15). The other markets had no cost
(`round_trip_bps_by_symbol: {}` refuses them). Set from the demo fills of 2026-10-25..11-06 (ICM/Vantage; median spread
at fill + commission + mean adverse slippage, each rounded **up**; `config/settings.yaml`):

| market | RT bps | | market | RT bps |
|---|---:|---|---|---:|
| XAGUSD | 3.5 | | EURUSD | 1.0 |
| XPTUSD | 5.0 (no fills; public spread x2) | | USDJPY | 1.0 |
| WTI (XTIUSD) | 3.0 | | GBPUSD | 1.0 |
| US500 | 1.0 | | AUDUSD | 1.5 |
| US100 (USTEC) | 1.5 | | GER40 (DE40) | 1.0 |
| JP225 | 1.0 | | UK100 | 1.5 |

Financing (W3.3) is unchanged: DTB3 or the OECD 3-month differential + **2.5 %/yr markup each side** for every market
without measured swaps (gold 0.74 %/yr measured). For a multi-day hold this markup, not the spread, is the dominant
cost. Triple rollover days from `config/instruments.yaml` (metals and FX Wednesday, indices and WTI Friday).

Report-only stress (never gates): the confirm result is also restated with every RT x3.

## A1.3 Power re-stamp (prereg section 3; P_explore = Phi(SR_prior x sqrt(Y_learn) - 2), Y_learn from bar dates)

The book priors use sr_blend(member, n = 13, rho 0.15).

| id | SR_prior | Y_learn | P_explore | P_chain | status |
|---|---|---|---|---|---|
| POS1-001 | 0.400 | 9.69 | 0.23 | 0.021 | member |
| POS1-002 | 0.467 | 9.69 | 0.29 | 0.037 | member |
| POS1-004 | 0.300 | 9.69 | 0.14 | 0.007 | member |
| POS1-006 | 0.400 | 9.69 | 0.23 | 0.021 | member |
| POS1-008 | 0.400 | 9.69 | 0.23 | 0.021 | member |
| POS1-015 | 0.400 | 9.69 | 0.23 | 0.021 | member |
| POS1-016 | 0.464 | 9.69 | 0.29 | 0.036 | member |
| POS1-025 | 0.572 | 9.69 | 0.41 | 0.085 | member |
| POS1-033 | 0.200 | 9.69 | 0.08 | 0.002 | member, informational |
| POS1-045 | 0.200 | 9.69 | 0.08 | 0.002 | member, informational |
| POS1-055 | 0.477 | 9.69 | 0.30 | 0.041 | member |
| POS1-056 | 0.400 | 9.69 | 0.23 | 0.021 | member |
| POS1-060 | 0.300 | 9.69 | 0.14 | 0.007 | member |
| POS1-026A | 1.006 | 9.43 | 0.86 | 0.570 | confirm-eligible |
| POS1-027A | 0.646 | 9.43 | 0.49 | 0.135 | member (P_explore < 0.50) |
| POS1-057A | 1.006 | 9.43 | 0.86 | 0.570 | confirm-eligible |

027A falls just under the 0.50 bar with 13 markets and is read in explore only, by the prereg's own rule. Confirm is
therefore m <= 2 (026A, 057A), fixed by `--write-confirm-set` from the EXPLORE JSON, committed before CONFIRM.

## A1.4 Pass rules, kill rule, budget (unchanged from prereg section 4, restated)

- **Explore** (LEARN-OLD 2003-05 + LEARN 2010-17 as one series): net > 0; block-bootstrap t >= 2.0; >= 60 % of years
  in market positive; drift-adjusted gross > 0; null (episode-sign) excess >= 0; >= 30 round trips; >= 500 days.
- **Confirm** (2018-01..2025-09, once): BH-FDR q 0.10 over the committed set; net > 0; >= 5 of 8 years positive;
  ex-2025 net reported. **TEST-OLD** (2006-02..2009, once, confirm passers only): net > 0, t >= 1.28.
- **Kill (frozen):** if 026A and 057A both fail confirm, the TREND-XASSET line closes today and is not re-run,
  widened or re-parameterised. Budget: this one pass (16 reads), 2026-11-06, no second pass.
- **Card (Rule 2).** Mechanism: slow-moving information and hedging-pressure trends across asset classes (MOP 2012,
  HOP 2017). Expected gross edge per round trip: weeks-long holds that move several % against 1-5 bp of round trip,
  above 2x the cost by two orders of magnitude; the binding cost is financing (2.5 %/yr markup). Turnover: about
  0.1-0.3 rebalances per market per day. **Expected net at prior SR ~1.0 and 10 %/yr book vol: about 10 %/yr of the
  equity at risk, i.e. ~GBP 0.40/day per GBP 1,000.** Far below GBP 50/day at today's size; sizing is owner-only.

## A1.5 Demo translation if it passes (pre-registered now)

A passer goes to demo as a **learning** arm (ADR 0061 amendment): its own book, the passing spec's exact rule on the
markets the engine can trade, the 21:00 UTC decision filled at the next open, matched random-sign control twin
(same schedule, same |units|, random side per episode, the null of A1.4), `loss_cap` GBP 50, `until` 45 days. Kill: net
below its control at 45 days or at the loss cap. Never real money without the live gate.
