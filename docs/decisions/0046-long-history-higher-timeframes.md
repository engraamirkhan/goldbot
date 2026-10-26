# 0046: Long-history higher-timeframe data: context and features, never synthetic intraday

Status: accepted (cto, 2026-10-26; owner delegated the call 2026-10-26). Review date 2027-01-31.

## Context
Owner idea (2026-10-26): get daily, weekly, monthly and yearly gold bars as far back as possible and "from there,
the smaller time frames may be able to calculate". The history we have or can get:

| Source | Span | Frequency | Repo/licence status |
|---|---|---|---|
| Dukascopy XAUUSD | 2010-2026 published (`data-v1`); 2003-2009 pulling (run 40384742150) | 1m from ticks | in use |
| TradingView OANDA:XAUUSD | from 2006-03 | 1m-1M (depth per timeframe being measured by the data-engineer lane) | personal pull, keyring cookie |
| LBMA/IBA fix | 1968 | twice-daily fix (a point price, not OHLC) | licence required; FRED copy removed 2022 |
| World Bank Pink Sheet | 1960 | monthly average | free, CC BY to confirm |
| DataHub `core/gold-prices` | 1833 (pre-1960 is an annual average repeated 12 times) | monthly | PDDL |
| No verified free daily source | 1968-2002 | | gap (survey `docs/research/data-sources-survey-2026-10-26.md`, section 1a) |

Gold was fixed at 35 USD until 1968, two-tier to 1971, and US citizens could not hold it until 1975. A free spot
market exists only from 1971.

The physics: a bar keeps four numbers of the path inside it. The order of high and low, every touch of a stop or
target, and the volatility inside the bar are gone. **Lower timeframes cannot be recovered from higher ones.** Any
intraday bar made from a daily bar (Brownian bridge constrained by OHLC, or any other fill) is a model's output, not
market data: a backtest on it measures the generator, and its fills, trade counts and costs are invented.

## Decision
1. **No synthetic bars as evidence.** Bars generated below the source's native resolution are never used to test a
   strategy, count trades, fit costs, train or score a model, or feed a registry trial, a gate or a report. They are
   allowed only as test fixtures that exercise code paths (gaps, extreme ranges, stop-and-target-in-one-bar), live
   under `tests/`, and carry `synthetic=True`; the data store refuses to load a synthetic frame into research or
   engine paths (enforced by a test).
2. **Long-history higher-timeframe (HTF) data is used for four things:**
   - *Context and regime:* long-run volatility regime, volatility cycles, trend state, drawdown-from-high, 30/60/90-day
     and seasonal (month-of-year, turn-of-month) behaviour.
   - *Point-in-time features for intraday models:* for example position in the 52-week range, distance from the 200-day
     mean, 1-year realised-vol percentile, monthly trend sign. Joined only with `store.asof_join` on `available_utc`;
     a daily bar is available at its close (canonical day boundary 17:00 New York) plus publication lag; a monthly
     *average* is available on its publication date, never at month start or month end. HTF features count against
     the 40-feature cap like any other.
   - *More independent periods for multi-day hypotheses* (swing 1d/1w): 1971-2026 gives about 660 non-overlapping
     30-day windows against about 200 from 2010 (about 290 from 2003).
   - *Regime robustness of intraday findings:* an intraday result is checked for whether its daily proxy (for example
     "the edge lives in high-vol, trending months") held across 1971-2009 regimes. This is supporting evidence only:
     verdicts on intraday rules stay on real intraday data, and every long-history test is a registry trial counted
     in K.
3. **Splicing sources across eras** (one series per frequency, provenance kept per row):
   - Overlap first: two sources are joined only over at least 1 year of overlap where daily log-return correlation is
     at least 0.99 and the median absolute close difference is within 10 bp (starting thresholds; the data-engineer
     measures and records the actual figures, and a failure blocks the splice).
   - Like for like: spot with spot. Futures (COMEX GC, Yahoo GC=F) are never spliced into spot levels; if used, only as
     roll-adjusted returns with roll dates recorded. A fix (a point price) is not an OHLC close; a monthly average is
     not a month-end close; neither is spliced into a close series.
   - Day boundary: each source's own convention is recorded (Dukascopy UTC, OANDA 17:00 NY, LBMA 15:00 London) and
     bars are rebuilt to the canonical boundary where we hold the finer data; otherwise the series is flagged and not
     mixed in return calculations.
   - Eras: pre-1971 (fixed or two-tier) is excluded from volatility and return statistics; pre-1960 annual values are
     level history only, never a feature.
   - Licence: only PDDL or confirmed CC BY data may go into a GitHub release (the repo and its releases are public);
     personal-use sources (TradingView, Stooq, Yahoo, MeasuringWorth) stay on the brain host and local disk.
   - Our own aggregation (1m up to 1d/1w/1M) is reconciled against an external daily series over the overlap; this
     is also the check of our 1m data.
4. A licensed daily source for 1968-2002 (IBA) is not bought now. It goes to the CFO and owner only if a
   pre-registered multi-day trial is blocked by the gap.

## Consequences
- The owner's goal (as much history as possible) is met for context, regime, seasonality and multi-day work. No
  intraday evidence is produced from it, so the number of trades and the intraday backtests stay honest.
- Research slices that benefit: **B (cycles)** most, with volatility cycles, seasonality and 30/60/90-day windows over
  5 decades; **C (fib/wavelet)**, with fib levels anchored on weekly and monthly swings and multi-scale *causal*
  wavelet features (two-sided filters stay banned, `docs/research/indicator-survey.md:181`); **H (combinations)**,
  with HTF regime as a conditioning input to intraday combinations (each conditioning is one more trial).
- New work, see backlog items 105 (long-history store with splice checks, data-engineer) and 106 (HTF context features,
  ml-researcher with the data-engineer). Each adds tests; the synthetic-frame guard is part of 105.
- Cost: no paid source; storage is small (daily since 1971 is about 14,000 rows).
- Reverse or revisit if: (a) HTF features add nothing out of sample after two quarterly research cycles, in which case
  stop maintaining pre-2003 data; (b) a verified free or licensed daily source for 1968-2002 appears, in which case
  rule 3 decides the splice; (c) a real intraday source before 2003 appears. Rule 1 (no synthetic evidence) is not
  reversible by a speed or volume argument.
