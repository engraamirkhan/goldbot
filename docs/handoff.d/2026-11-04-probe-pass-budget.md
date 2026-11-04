- **Multi-book pass under its budget (2026-11-04; live demo issue, icm-probe: "pass budget 5.0 s spent after
  8|16|19 of 20 books").** Measured first with `scripts/bench_account_pass.py`. It runs icm-probe's 20 books and
  registered arms on a paper terminal with 60 days of synthetic 1m history, one `account_pass` a second, counts every
  bridge call and has optional `--latency-ms` and `--profile`. Bridge calls are not the cost: about one `ticks_many`
  a pass. CPU is: the 15m close's pass took 11.3 s on the first close after a start and 4.2 s warm (Mac). Changes:
  1. `Frame.X` (new `engine/frames.py`) builds the features on first read. Only agents, the shadow book and blackout
     re-scores read them, so the 19 non-gold books (no agents) no longer build them. The regime is computed only on a
     book with agents (its only reader).
  2. `IncrementalResampler` compares datetime columns as epoch ns. Before, it built a Python Timestamp for every row
     of `visible_at` on every call.
  3. `history.window_gaps` keeps each hole's answer in a per-book `GapMemo`. Before, every close of every book
     re-judged the same weekend holes minute by minute. A new session table, covered span or limit clears the memo.
  4. `mid()` builds its frame in one go.
  After (same harness): 15m close 1.9 s cold and 0.64 s warm; minute passes 190 ms against 202 ms. The budget,
  `PASS_BUDGET_S` = 5 s, is unchanged. Key zones are left as they are: one build per 15m close on gold only, on its
  own thread, 0.11-0.16 s on 800 days of 15m bars here. The brain's logged 1.6-1.8 s suggests the brain runs about
  10x slower than this Mac, so warm 15m closes should now take about 6 s there instead of about 40 s.
  Decisions are bit-identical: `tests/test_pass_budget.py` runs a 33-minute, 3-book session with icm-probe's arms
  and real entries twice, through these fast paths and through reference copies of the old code. Every journal row,
  deal, position, gap and queued signal must match. `tests/test_perf_equivalence.py` covers the resampler and `mid`.
  One behaviour change, on the error path only: a feature build that raises no longer fails the bar close of a book
  without agents, because that book never builds the features. A book with agents fails as before.
  Not done:
  - The 1m S0 rule (`arm_rules.random_bracket`) scores its whole lookback every minute, though the engine takes only
    the last bar. This is about 5 ms a book a minute on the Mac and is the next lever; it needs quant review.
  - Not measured on the brain itself.
  Review: trading-safety (bar-close path: lazy features, regime only with agents).
