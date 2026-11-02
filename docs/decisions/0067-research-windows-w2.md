# 0067: Research windows W2: learn 2010-2017, confirm 2018-2025-09, pre-2010 learn_old/test_old blocks

Status: accepted (data-engineer for the program director, 2026-11-02; owner directive of the same day)

## Context
Owner, 2026-11-02: "it should learn on 2010-2017 and get one test on 2018-2025 and even more historical data by also
keeping the test data from subset of it". Dukascopy serves XAUUSD 1m from 2003-05-04, but only 2010+ was on disk (the
2003-2009 Actions run crashed, issue #108, and market data is local-only since the public `data-v1` was deleted).

A first amendment (W1) split 2003-2009 into odd years for learning and even years for testing. The statistics review
rejected it before any pre-2010 bar was read: 2003-2009 is one gold bull regime, so alternate years share their
drivers and autocorrelated state (trend, volatility level, month-scale features), and an even-year test is not
independent of odd-year learning. W2 replaces W1 with contiguous blocks and an embargo. W1 was never used.

## Decision
Windows (UTC, end exclusive), frozen before any pre-2010 bar or sprint3000 result was read:

| window | span | use |
|---|---|---|
| learn_old | 2003-01-01 .. 2006-01-01 | extra learning data, new trials only (in-flight lanes do not add it) |
| embargo | 2006-01-01 .. 2006-02-01 | never read as a window |
| test_old | 2006-02-01 .. 2010-01-01 (2008 crisis included) | held-out test, read once per strategy, only after it passed confirm |
| learn | 2010-01-01 .. 2018-01-01 | learn / screen (unchanged) |
| confirm | 2018-01-01 .. 2025-10-01 | one test (unchanged) |
| holdout | 2025-10-01 onward | sealed (unchanged) |

- A trade belongs to the window of its ENTRY time; indicator warm-up may read up to 366 days of earlier bars,
  whatever their window, never a holdout bar.
- One code path: `goldbot.research.windows` (`bounds`, `years`, `window_of`, `entry_mask`, `read_window_bars`,
  `allow_test_old_for`). test_old opens only with `allow_test_old=True`, which scripts obtain from
  `allow_test_old_for(id, confirm_passed)`; holdout and embargo raise whatever the flags. Guards are exceptions, not
  `assert` (which `python -O` strips).
- Qualification rules and costs for these windows are in the W2 amendment (sprint3000 brief): qualified = explore +
  confirm + test_old; test_old missing on disk means "old-test pending", no deploy.
- The pre-2010 years are fetched on the Mac with the existing fetcher (`scripts/dukascopy_year.py --pause 0.5
  --no-node-fallback`; `dukascopy_months.FIRST_YEAR` is now 2003) into `raw/` only. Never published to a release or
  artifact (Dukascopy terms: personal use, no redistribution).
- The earliest usable year is chosen by `scripts/bar_quality_years.py` on bar quality alone (gap days <= 2% of
  weekdays, median minutes per trading day >= 1,000, in-session gap>1m steps <= 10%, imputed <= 5%, spread p50 in bps
  <= 3x the 2010-2017 median), thresholds fixed in this ADR before the pre-2010 files were read. No return, label or
  strategy runs on test_old for this choice.

## Consequences
- Seven more years, including a second regime (2003-2009 bull, the 2008 crash) for the rules that survive confirm.
- The 2006-01 embargo costs one month; month-scale features that straddle 2005/2006 cannot leak learn_old into
  test_old through overlapping labels.
- Early Dukascopy gold is thinner: gaps inside a session let price jump through a stop, so overshoot measured in
  2010+ understates pre-2010 slippage. The quality report records the in-session gap share per year; runs on old
  windows must use the bps cost model of the amendment and report gap-through-stop counts.
- Pre-registrations pin bar digests by year: adding pre-2010 files does not change any pinned 2010+ digest, and an
  old-window run pins its own years.
- What would change this: a test_old block found to share a data-quality artefact with learn_old (for example the same
  feed change), or a quality verdict that leaves fewer than two usable test_old years; either goes back to the
  research director before any test_old read.
