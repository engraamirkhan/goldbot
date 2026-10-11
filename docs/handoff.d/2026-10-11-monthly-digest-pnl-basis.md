- **Monthly report and daily digest: net means net (2026-10-11; finance audit GAP-05, quant review pending).**
  Before this, `goldbot/ops/monthly_report.py` and `goldbot/telegram/digest.py` added every closed trade's P&L into
  "net", including rows whose commission and swap were never recorded. They now use the same rule as
  `perf_report.py`. The rule lives in one place, `gates_phase.pnl_basis`; `perf_report` imports it from there.
  - **Net figures use net rows only.** A net row has `pnl_basis` "net", or is a legacy row that records both
    commission and swap. This covers money, R, the chained return, drawdown, best days, £50+ days, the average per
    trading day and the digest's won/lost count.
  - **Every other row is shown apart and summed as gross.** That is `pnl_basis` "gross", or a legacy row without both
    costs. The wording is "N trades gross only (costs not recorded)", with the gross money and R. The monthly report
    adds "left out of every figure above", the month-so-far line adds "not summed", and a day with only gross rows
    says "net not recorded". No gross figure carries the word net.
  - **Change the owner will see.** Trades from the engine's own fills, where no broker deals were read, have
    `money_source` "engine" and no costs. They now show as gross only and leave the net totals. So do legacy records
    from before the closed-trade record carried commission and swap. Net totals can therefore drop compared with
    yesterday's digest.
  - **Day convention, stated in the report.** A day is the UTC calendar day, 00:00-24:00. That is the risk day on
    which the loss caps reset (`risk_day_anchor`) and the digest's "yesterday" (`MonthlyReport.day_boundary`, the
    report's second line). `perf_report` uses the opportunity day, which rolls at 22:00 UTC, so a close between 22:00
    and 24:00 UTC falls on different days in the two reports. We kept the UTC day so that the monthly £50 days line up
    with the daily loss cap and the digest.
  - **New health check `pnl_basis:<account>`.** It WARNs when more than 20% of the account's closed trades over the
    last 30 days (by exit) are not net (`PNL_BASIS_GROSS_WARN_PCT`, `PNL_BASIS_WINDOW_DAYS`). It never FAILs, even
    on an unreadable record, and it does not notify on WARN. Why 20%: past one trade in five, the net totals cover
    too little of the month's trading to be read as its result. The usual cause is an unreadable broker deal history
    (check the bridge).
  - **Not changed (flagged for the quant reviewer).** `gates_phase` (the phase gates and the stop rule's loss
    series), `research/ruin.py` and `ops/ledger.py` still read every row's `pnl` without regard to its basis. The
    phase gate is out of bounds for this change.
  - Tests: tests/test_monthly_report.py (6 new), tests/test_digest.py (2 new), tests/test_health.py (6 new). Net
    fixtures in tests/test_currency_followups.py, tests/test_excursion.py and tests/test_sizing_review_fixes.py now say
    `pnl_basis="net"`. Two sizing-review assertions on engine-fill trades now expect those trades as gross only.
