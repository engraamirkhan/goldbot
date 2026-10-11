- **Performance report for the analysts (2026-10-11; needs quant review).** `python -m goldbot.ops.run perf-report
  [--state DIR] [--data-root DIR] [--from D] [--to D] [--account ID] [--out DIR] [--json]`, also
  `scripts/performance_report.py` for a state folder copied to the Mac. It reads the state only (closed trades,
  approvals/done and pending, shadow_book.json, population.json, engine_ and costs_ files). The optional data root
  adds the stored bars, fills and journal. It writes `perf_<from>_<to>.md` and `.json` to `--out`, default `reports/`
  (now gitignored), and refuses an `--out` inside the state folder or the data root. Days are opportunity days
  (22:00 UTC rollover).
  - Sections: net and gross R and money by family, agent, timeframe, session, side and day type; owner decisions,
    with the shadow outcome of the rejected, expired and approved signals; realised against modelled costs;
    opportunity-day capture against the £50 north star; drawdown against the limits; the journal.
  - Every row carries its count. Below 30 trades a row says "insufficient sample" and gives no interval or reading.
  - Missing fields show as "not recorded". The record's finance-audit fields are used when present.
  - No identifiers reach the output: no position or order ids, broker or server names, proposal ids, or who
    decided.
  - Tests: `tests/test_perf_report.py` (22). Row V7.
