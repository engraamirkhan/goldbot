# Pre-registration: growth screens C1, C2, C4 (exits researcher, 2026-11-01)

Frozen by commit before any 2010-2017 outcome is computed. Parent: `growth-sprint-2026-11-01.md` section 2 (research
director, PR #251), which freezes the candidates, the pass bar and K += 7 (3,002 -> 3,009: C1 x2, C2 x2, C3 x2, C4 x1;
C3 is run by another lane). This file only fixes the operational details section 2 leaves open, so the screen code has
no free choice after the data is seen. Results: `growth-screens-2026-11.md`. Code: `candidates/growth/growth_screen.py`.

**Superseded, not run.** The owner's own three rule texts of 2026-11-01 (trend-day pyramid at 0.5 x ATR15 with a
2 x ATR15 trail; NR7/inside bar on 1h/4h with a 3 x ATR trail; news straddle at +-1 x ATR15 with a 2 x ATR15 trail) were
replaced by the director's frozen C1/C2/C4 before any code ran. They were never computed: K +0 for them.

## Common

| Item | Frozen value |
|---|---|
| Window | 2010-01-01..2017-12-31 only, `raw/xauusd_1m_dukascopy_2010..2017.parquet` (local, not committed); the loader asserts no 2018+ bar. Confirm 2018-01..2025-09 and holdout 2025-10..2026-09 untouched |
| Prices | 1m mid = (bid + ask) / 2. Decisions on bars complete at decision time; fills at the next 1m open; a stop or entry level gapped through fills at that minute's open (worse) |
| ATR15 | ATR(14) (simple mean of true range) of 15m mid bars, the last completed 15m bar before the decision |
| Cost | `config/cost_of_record.yaml` version **cor-2026-10-31.2**, ICM cell of the entry session (asia < 07:00, london 07:00-12:30, newyork >= 12:30 UTC): **1.5 x round_trip_usd per unit**, plus **1.5 x overshoot_usd per unit on a stop exit**. Long swap prior $0.60/oz per 22:00 UTC rollover held (settings `swap_long_usd_per_lot` -60, triple on Wednesday), shorts 0 |
| R | net USD per oz over the initial stop distance of one unit. A pyramided trade's R is the sum over its units, in units of one unit's initial risk |
| Random twin | 4 twins per real trade, same day, same mechanics (stop distance from that twin's own ATR, adds, trail, target, exits), seeded from sha256(variant, day, i) |
| Pass (each variant) | (a) net mean R, day-block bootstrap (10,000, seed 20261101), 90% lower bound > 0; (b) real minus mean-twin R per day, one-sided bootstrap p, **Bonferroni over 7** (7p < 0.10; Holm over 7 can only be looser, so a Holm-only pass that depends on C3's p is reported, not claimed); (c) >= 5 of 8 calendar years with net mean R > 0. All three, else FAIL |
| Reported | n, trades/week, mean, sd, skew, P(R >= +5), top-decile share = sum of the top 10% of R / sum of all R, whipsaw (C1); weekly virtual ledger at f = 5/10/20% of equity per 1 R, compounded trade by trade in exit order within each Monday-Sunday UTC week, floored at 0, every calendar week counted (no trade = x1.0): P(week >= x2), P(week <= x0.5), median multiple, geometric-mean weekly multiple |

## C2 pyramided session-range breakout (2 variants)

| Item | C2a Asia -> London | C2b London -> NY |
|---|---|---|
| Range | high/low of 1m mid, 23:00 (prior day) - 07:00 UTC | 07:00 - 12:30 UTC |
| Break window (15m closes) | 07:15 .. 12:30 | 12:45 .. 19:00 |
| Session end (flat) | 16:00 UTC | 21:00 UTC |

The first 15m close beyond the range per day sets the side; entry at the next 1m open. Initial stop 1.5 x ATR15 (= R).
Adds at entry +1, +2, +3 R (fill at the level or the gapped open), max 4 units. On each add the common stop becomes
max(previous stop, avg_price - R / units) for a long (mirror for a short), so open risk <= 1 unit. Within a minute:
adds first, then the stop against the stop in force at the minute's start (conservative). Exit at the stop or at the
session end's last 1m close. UTC clock, no DST shift. Twin: entry at a uniform random 15m close in the same break
window, fair-coin side.

## C4 NR7 day, prior-day-range break, 4 R target (1 variant)

Trading day = 22:00 UTC to 22:00 UTC. Day D is NR7 if its high-low range is the smallest of D-6..D. On D+1, buy-stop at
D's high and sell-stop at D's low (OCO, first touch, live for D+1). Stop = the other side of D's range (1 R = D's range);
target = entry +- 4 R; time exit at the close of the 5th trading day after entry if neither is hit. A minute touching
both entry levels counts as a long stopped out (conservative). Twin: a random minute of D+1, fair-coin side, same R,
target and time exit.

## C1 news straddle (2 variants: k = 0.5, k = 1.0)

Events: calendar-v1 (`~/tmp/ts-data/cal/us_calendar.parquet`, local) tier-1 `nfp`, `cpi`, `fomc_statement`, status
scheduled, event_utc in 2010-2017 (PCE and the press conference excluded, as in section 2). Reference = mid close of
the 1m bar opening at T-2 min (the price at T-1). OCO buy-stop ref + k x ATR15, sell-stop ref - k x ATR15, live from
T-1 to T+30 min, then cancelled. Filled side: initial stop = the opposite order level (R = 2k x ATR15), trailed at best
mid - 1 x ATR15 (long; mirror short), never loosened. Exit at the stop or at the close of min(T + 240 min, 21:00 UTC).
**Release-minute slippage:** a fill or stop exit in the 1m bars opening at T-1, T or T+1 pays an extra 3 x ICM newyork
spread_usd ($0.258/oz) on that leg. Whipsaw = the opposite order level is touched after the fill and before T+30 min;
whipsaw > 45% falsifies the variant whatever the bar says. Twin: the same OCO at a uniform random minute of
13:00..19:00 UTC on the same day, at least 90 min from any tier-1 event, no release-minute charge.

## Reading rule (written before data)

A variant passes only on (a), (b) and (c). A pass earns one confirm trial on 2018-2025-09 (director's slot) and nothing
else; no parameter is tuned on this read. Fail on all five: C1/C2/C4 are retired as specified and the doubling goal has
no convex candidate from this set. The growth figures are descriptive whatever the verdict; at a non-positive net mean
they show what bet size does to zero edge and are not a reason to trade. Risk of 5-20% per trade is above the 1%
RiskGate design limit: sizing is owner-only and nothing here changes it.

Trial cost: K +5 of the director's +7 (explore screen, no registry trials). Gates touched: none.
