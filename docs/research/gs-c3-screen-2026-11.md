# Growth sprint C3 screen: intraday momentum, New York and London (2010-2017)

Run 2026-11-01 by the opportunity-researcher for the growth sprint (`growth-sprint-2026-11-01.md` §2, candidate C3,
PR #251). Two variants, one look each, on bars 2010-01-01..2017-12-31 only (local Dukascopy 1m, years 2018+ never
read; holdout sealed). Screen, not a registry trial: **K +2** of the sprint's 7 (3,002 -> 3,009 when all four screens
report). Section 1 was committed before any output was computed.

**Prior, stated up front.** C3 re-tests the shape of retired family **R-05** (intraday_momentum: NY -0.006 R gross,
t -0.50; London -0.028 R, t -1.48; retirement reconfirmed 2026-10-18) and sits next to R-17 (trend-day continuation,
gross -0.003 R). The new element is the Gao, Han, Li and Zhou (2018, *Journal of Financial Economics* 129(2),
US equity ETFs 1993-2013, gross of costs; single-paper for gold, practitioner only) construction itself: a short
opening window predicting a short closing window, not R-05's mid-session decision with a 3 ATR target. The research
director placed it in the sprint; my expectation is FAIL.

## 1. Frozen rule (committed before any data was read)

Sessions are `goldbot.data.calendar.LOCAL_SESSIONS` (DST from the zone): London 08:00-16:30 Europe/London, New York
08:30-16:00 America/New_York. Prices are the 1m mid (bid+ask)/2; costs below carry the spread.

| Item | V1 New York | V2 London |
|---|---|---|
| Formation return | mid close of the 08:59 bar minus mid open of the 08:30 bar (first 30 min) | close of 08:59 minus open of 08:00 (first 60 min) |
| Entry | open of the 15:00 NY bar (the last 60 min) | open of the 09:00 London bar (formation end) |
| Side | sign of the formation return; zero = no trade | same |
| Time exit | close of the last bar before 16:00 NY | close of the last bar before 16:30 London ("predicts London close") |
| Skip the day if | the opening, formation-end or entry minute is missing, or ATR is undefined | same |

- **Stop and R.** Protective stop 1.5 x ATR(14) of 15m mid bars that closed at or before the entry (point in time;
  same multiple as C2). 1 R = the stop distance. Stop checked on 1m mid high/low; a 1m open through the stop fills at
  the open (so a loss can exceed 1 R). No target.
- **Cost (the bar's 1.5 x).** `config/cost_of_record.yaml` `cor-2026-10-31.2`, icm, session cell: 1.5 x
  round_trip_usd (NY 0.166, London 0.164 $/oz) on every trade, plus 1.5 x overshoot_usd (NY 0.13, London 0.085) on
  stop exits. Swap 0 (flat within the session). Net R = gross R - cost / stop distance.
- **Matched random-entry twin.** Same day, entry minute, exit minute and stop distance; direction a fair coin. It is
  evaluated in expectation (the mean of the long and the short outcome, net), which equals the limit of infinitely
  many seeded draws. Excess_d = strategy net R - twin net R.
- **Statistics.** One trade a day, so the day bootstrap is an iid resample of days: 20,000 resamples, seed 20261101.
  90% lower bound = 5th percentile of the resampled mean. Excess one-sided p = share of resampled means <= 0.
- **Pass (all three, per variant):** (a) net mean R 90% LB > 0; (b) excess LB > 0 Holm-adjusted over the 7 sprint
  variants: p <= 0.05/7 = 0.0071 passes under any ordering; 0.0071 < p <= 0.05 is reported as "depends on the other
  screens"; p > 0.05 fails; (c) net mean R > 0 in >= 5 of the 8 years.
- **Reported, not in the rule:** gross mean and t, long/short split, stop share, cost in R, by-year table; virtual
  weekly ledger at f = 5/10/20% of equity per 1 R (equity x prod(1 + f r) over the week's trades in time order,
  per variant and for both together): P(week end >= 2x), P(week end <= 0.5x), and P(touch 0.5x within the week).
- **Reading rule.** PASS -> one confirm on 2018-01..2025-09, the director's call. FAIL -> the R-05 shape is closed a
  third time at 1m/15m: no further session-momentum timing, window or exit variant without new evidence (a new
  mechanism, not a new window). Losers are reported with winners.
