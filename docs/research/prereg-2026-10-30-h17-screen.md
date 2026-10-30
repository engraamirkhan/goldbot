# Pre-registration: H-17 screen on 2010-2017 (amendment A3, bet 2 of the cycle-2 plan)

Written 2026-10-30 by the opportunity-researcher, **before any H-17 output on any window was computed** (no event
count, no exit walk, no return). Base `dd39931f` (origin/release/agents-rd-cto merged). H-16's screen has a recorded
result (`opportunity-days-screen-2026-11.md`, FAIL; HAR-only is the day factor), which A3 requires first; H-17 reads
the HAR baseline only, so that verdict does not change H-17's configuration.

## What is screened

H-17 exactly as pre-registered: `hypotheses.md` row 4, `preregistration-2027Q2-draft.md` "H-17" (config, pins P-2 and
the literal blackout, quant ruling Q-1), `preregistration-2027Q1.md` O-2/O-3. Restated:

- **Day condition:** high when H-16's HAR-only baseline forecast for D (`day_type.har_forecast`, monthly expanding OLS,
  1-day purge, >= 500 rows) is >= its fit's in-sample cut50, joined as of `available_utc` (the emitting day's close);
  low otherwise; no forecast yet = unclassified (not counted). Fits are walk-forward inside 2010-2017.
- **Reference open** O_D = open of the 15m mid bar starting 08:00 London. **Noise boundary** B at London close time
  tau = mean of |close at tau - O| over the previous 14 days that had that bar; fewer than 14 = no B.
- **Entry:** the day's first 15m bar whose close is in [09:00 London, 10:30 New York] (own DST clocks), with B defined
  and |close - O_D| > 1.0 B; side = sign(close - O_D); entry at that close (mid). **Literal first breach:** if that
  bar has no ATR(14) or its close lies in a tier-1 blackout ([-15, +30) min around a release scheduled as known at the
  bar, from the calendar's `available_utc`), the day has no entry. At most one trade a day.
- **Stop** 1.5 x Wilder ATR(14) of 15m mid bars at the entry bar, frozen; R = that distance.
- **V0:** target 3.0 ATR, else flat at the first bar close at or after 16:30 New York.
- **V1:** 50% off at 2.0 ATR; chandelier 2.5 ATR from the best price, armed at +1.5 ATR; cap 10 ATR; flat 16:30 NY;
  R = size-weighted blend.
- **Low half (shadow):** the identical rule on low days, for the Welch test only.

## Implementation choices fixed now (not in the earlier text; none is tunable after output)

- Exits walk **1m mid bars** from the entry instant to the 1m bar ending 16:30 NY (the engine labeller's convention).
  Inside a bar the stop is checked first (conservative); a stop fills at min(level, bar open); limits fill at their
  level; the trail uses the best price of **completed** bars. If no bar exists at 16:29 NY before 17:00 NY, the last
  bar of the session closes it (counted). Same-bar stop/target ambiguities are counted.
- **Costs (cost of record, evaluator ruling R2):** $0.23/oz round trip on every trade plus $0.70/oz on the size that
  exits by a stop order (initial stop or trail). H-17 has no demo arm, so there is no signal-time fill cost to take the
  worse of. Reported only: the research prior, $1.07/oz flat.
- **Swap = 0 by construction:** every entry is after 09:00 London and every exit is by 16:30 New York on the same date,
  before the 17:00 NY rollover. Nothing is held overnight.
- `policy_step`/`flat_at_local` (BACKLOG 31) and E-3 `trend_day` are not merged; the screen uses the written rule in
  `scripts/research/h17_screen.py` ("spec-derived" in the Q2 draft's terms). The formal trial must recount with
  `trend_day`'s own code.
- Calendar: the local keyless build `~/tmp/ts-data/cal/us_calendar.parquet` (sha256 `7188df20`; tier 1 = NFP, CPI,
  PCE, FOMC statement and press conference), cut at 2018-01-01 on `available_utc` and `event_utc`.

## Frozen code and inputs (quant condition 1)

- Driver `scripts/research/h17_screen.py` (sha1 prefix printed as `code_hash` in every output), reading
  `goldbot/features/day_type.py` (`VERSION = "1"`) and `goldbot/research/h16.py` (`cut_before_holdout`) unchanged.
  All are frozen by **the commit that adds this file**; the result document records that hash.
- Bars: `raw/xauusd_1m_dukascopy_2010..2017.parquet` only, sha256 prefixes 2010 `26139f0b`, 2011 `a1497c69`, 2012
  `89f4b77d`, 2013 `fe31b844`, 2014 `be2bf066`, 2015 `046ddd84`, 2016 `9c6073cc`, 2017 `e04d7da0`; cut at 2018-01-01
  before anything is built; the script exits if a 2018+ bar appears. No holdout, no 2018+ file.
- Order: `--stage count` (events only) is run and its counts written into the result document **before**
  `--stage read` is run. The exit walk was checked only on synthetic paths (`--stage selftest`) before this freeze.

## Reading rule (O-2/O-3 and Q-1, unchanged in substance; A3 window)

Statistics on the high half's strict events, t = mean / (sd / sqrt n) (one trade a day, so iid t = day-clustered t).

- **Event floor for the screen: 400 strict high events.** Derivation (stated before the count): the frozen floor is
  1,000 on 2012-04..2025-09 (13.5 years); the screen's out-of-sample span is about 2012-03..2017-12 (5.8 years), so
  pro rata 430; A3 scaled the trial's floor by the same discount (574 pro rata -> 500), giving about 375; rounded to
  400. Below 400: **inconclusive**, the screen cancels nothing and the formal trial keeps its own conditions.
- V0 and V1 each **pass** their screen if gross t >= 2.0 on >= 400 events, net mean R > 0, and >= 3 calendar years
  with positive net mean R. **Deviation, stated now:** the clause "including 2021 or 2022" cannot be read on
  2010-2017 and is deferred to the formal trial; it is not replaced by another year.
- **Welch (Q-1):** high-half minus low-half gross mean R over ISO-week blocks (each week's trades averaged per half),
  unequal variances, t >= 2.0 = "low half below high half". Computed per variant on that variant's gross R.
- Outcomes:
  - **FAIL** if V0 fails its screen, or V0's Welch t < 2.0 (repeat of R-02/R-05). V1 is then recorded as run and not
    read. Formal O-2/O-3 **cancelled**; the trend-day line retires (fail is final, Q-1).
  - **PASS (runner)** if V0 passes with Welch, and V1 passes with its own Welch t >= 2.0 and V1 net >= V0 net: O-2 and
    O-3 may be pre-registered on 2018-01-01..2025-09-30.
  - **PASS (fixed only)** if V0 passes with Welch and V1 does not meet the runner branch: O-2 only; O-3 cancelled; the
    runner is retired for this family.
- **Quant condition 2:** a screen can only cancel the formal trial; a pass is not evidence for adoption, only
  permission. Any change to rule, features or code after this commit is a new pre-registration with a new id.

## Sample floor for the 2018-01-01..2025-09-30 trial (quant condition 3)

>= **500 strict high events** on 2018-01-01..2025-09-30 (A3), counted with `trend_day`'s own code, events only, before
the trial's freeze; below 500 both rows are withdrawn (no trial spent). Not counted here: no 2018+ data is read.

## Reported, not in the rule

Net and gross by year; long/short split; exit-reason shares; ambiguous bars; mean cost in R and the median R in $;
net at the $1.07 prior; the next-bar-outside-blackout event count; the low half's means.

## Counting and cost

K +2 (V0 and V1; a screen, counted, not a registry trial). The registry, the quarter's budget, the reserved slots and
the holdout are not touched. Run time: minutes on the Mac, no paid resource.
