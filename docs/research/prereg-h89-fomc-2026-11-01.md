# Pre-registration H-89: post-FOMC continuation, one confirm look (2026-11-01)

Strategy researcher. Frozen before any 2018+ bar is opened. Source: `decision-2026-11-01-next-lines.md` (PR #247)
line 1. Code: `scripts/research/h89_fomc_confirm.py`, frozen at the commit that adds this file (hash recorded in
the result doc `h89-fomc-confirm-2026-11.md`; any later edit to the script voids the read).

## Hypothesis and mechanism
After a scheduled FOMC statement, the direction of gold's first five minutes continues over the next hour: the
policy news is digested slowly, and intraday momentum is strongest on news days (Gao, Han, Li & Zhou 2018, JFE,
"Market intraday momentum", SPY 1993-2013, gross; an adjacent asset, not gold). Gold is the rate-sensitive asset.
Falsified if 2018+ (press conference at every meeting from 2019) shows no drift.

## Explore evidence (2010-2017, already seen)
- Director (`decision-2026-11-01-next-lines.md`): n 64, mean continuation +$3.90/oz (t 3.98), 8 of 8 years
  positive, 73% positive; net at Dukascopy bid/ask +$3.49 (t 3.58).
- Independent quant recompute (bars indexed on `visible_at`, entry at the next bar's open): net at bid/ask, entry
  t+5m +$3.55 (t 3.47); entry t+6m +$3.11 (t 3.21); 8/8 years positive. Placebo (non-FOMC Wednesdays, same clock)
  -$0.08 (t -0.59). Dropping the top 5 events: +$1.92. Scripts `rc.py`, `ev.py` (session scratchpad, not committed).
- Code sanity replay of THIS frozen script on 2010-2017 (all costs below): n 64, mean net +$3.00, t 3.12, 7/8 years,
  70% positive, top-5 removed +$1.47. It reproduces the explore within the indexing difference.

## Frozen rule
- Events: `fomc_statement` rows of the keyless US calendar (`~/tmp/kcal_out/us_calendar.parquet`), scheduled
  statement minute in UTC, duplicate minutes dropped (2020-03-18 appears twice). Statement times verified: every
  2018-2025 statement is 14:00 America/New_York, converted with DST (18:00 UTC in EDT, 19:00 UTC in EST). Unscheduled
  meetings (2020-03-03, 2020-03-15) are not in the calendar and are not added. Press-conference era (every meeting
  from 2019) noted, not tuned: no sub-split is a pass leg.
- Bars: Dukascopy XAUUSD 1m bid/ask, labelled by bar open (`ts_utc`), the same indexing as the explore.
- Signal: side = sign(mid close of bar t+5m - mid close of bar t-1m); known at t+6m wall clock. side 0 = no trade.
- Entry: close of bar t+6m at ask (long) / bid (short) (one minute of extra latency over the explore).
- Exit: close of bar t+60m at bid (long) / ask (short). No stop, no target.
- Cost: bid/ask spread (in the fills) + commission $0.08/oz round trip (cost of record cor-2026-10-31.2) + $0.30/oz
  slippage margin.
- Coverage skip: fewer than 60 bars in [t-30m, t+60m] (the explore's rule); skips are reported.
- Window: 2018-01-01 .. 2025-09-30 (8 calendar years, 2025 has 6 meetings). Holdout 2025-10-01 on stays sealed: the
  2026 file is never opened and the script asserts no bar or event at or after 2025-10-01.

## Reading rule (decided now)
PASS only if all three legs hold on net $/oz per trade:
1. mean net > 0 with t (mean / (sd / sqrt n)) >= 2;
2. >= 5 of the 8 years 2018..2025 have mean net > 0;
3. share of trades with net > 0 >= 55%.

Any leg failing: H-89 is retired, final; not re-tested without new evidence (a new mechanism or data source, not a
new window, horizon or entry minute). PASS: the holdout (2025-10 on) is read once, then a 0.01-lot demo arm, both
subject to line 2 of the decision doc (release-minute round trip <= $1.00/oz). Informative only, not a leg: median
net, mean net with the top 5 events removed (fat-tail check), per-year means.

Expected under the explore: about 62 trades, mean net ~ +$2.7/oz at these costs if the effect persists unchanged;
t >= 2 needs roughly the explore's signal-to-noise. Prior of a pass ~30% (decision doc).

## Multiple-testing count
- Explore look, recounted honestly: 14 event types x 2 directions = 28, plus the unregistered choice of the 5m
  signal window and the 60m hold = **K 30** for the explore (the decision doc counted 15).
- This confirm: **1 registry trial, K +1**. Ledger before the memo ~2,971 -> 2,971 + 30 + 1 = **~3,002**.
- Registered as ONE registry trial (registry_sync idiom; Mac publish path from PR #243 if the brain cannot upload).
