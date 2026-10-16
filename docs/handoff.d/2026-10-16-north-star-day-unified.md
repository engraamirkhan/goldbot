- **One north-star day for the owner's reports (2026-10-16; TRACEABILITY V9, BACKLOG 55; quant and trading-safety
  review pending).** Before this, the monthly report used the UTC calendar day. The performance report used the
  opportunity day, which rolls at 22:00 UTC. Each had its own day-bucketing and weekday-count code, so a close between
  22:00 and 24:00 UTC landed on different days in the two reports. Both now use `goldbot/ops/report_days.py`
  (`report_day`, `weekdays`, `month_days`, `day_buckets`, `DAY_BOUNDARY`). The one definition of the day,
  `opportunity_day` with `ROLLOVER_UTC_H = 22`, moved from `features/day_type.py` to `goldbot/data/timeutil.py`, next
  to `risk_day`. `day_type` re-exports it, so the features, H-16 and their goldens are unchanged.
  - **The day.** Day D runs from D-1 22:00 UTC to D 22:00 UTC. That time is inside gold's 17:00-18:00 New York break
    in both US DST regimes, so the Sunday reopen counts on Monday and the Friday close on Friday. Tests pin this
    around 22:00 UTC and in the March and November DST weeks (including the US-EU gap week).
  - **Changes the owner will see in the monthly report and the digest's month line.**
    - A close between 22:00 and 24:00 UTC now counts on the next day. That moves best days and £50+ days.
    - The month follows the day too. A close at 23:30 UTC on the month's last day is in the next month's report.
    - The default month is the month of now's opportunity day. At 31 Oct 23:00 UTC that is November.
    - Trading days are the Monday-Friday opportunity days elapsed, today's included.
    - The digest's "month so far" (built at `now - 1 day`) now runs to yesterday's 22:00 UTC roll. A close after the
      roll waits for the next digest.
    - The report's second line now reads "days run from 22:00 UTC the evening before (the opportunity day; the Sunday
      reopen counts on Monday)". "(UTC)" is gone from the title line.
  - **Not changed.** pnl_basis: both reports pick their rows (net only, account currency only) before they bucket
    them, as they did before. The loss caps stay on the risk day (00:00 UTC). So does perf_report's drawdown against
    the limits (`risk_day`, `_risk_week`). No limit, RiskGate check or engine code was touched.
  - **Known gap (digest, not in this lane).** The digest's "Yesterday" line is still the UTC day, 00:00-24:00. A close
    at 23:00 UTC is in yesterday's trades line but in today's month line.
  - **Known gap (DESIGN.md, not edited: this lane writes fragments only).** "Time and calendar" says the fourth day is
    used "only by the opt-in day_type features". The owner's reports now use it too.
  - **Finding: `risk_day_anchor` is read by no code (for the quant and trading-safety reviewers).**
    - Where it is: `Settings.risk_day_anchor` (`goldbot/config.py:491`) and `config/settings.yaml:7`
      (`{ tz: UTC, time: "00:00" }  # loss caps reset`). No code under goldbot/ or scripts/ reads it, and
      `feature_day_anchor` is unread too (`resample.py` uses its own defaults).
    - Where the 00:00 UTC risk day is actually decided, each place on its own:
      - `timeutil.risk_day` (`goldbot/data/timeutil.py:163`), used by `gate.new_day` (`goldbot/risk/gate.py:537`),
        `throttle.entries_on_day` (`goldbot/risk/throttle.py:83`) and perf_report's drawdown;
      - the engine's own UTC `.date()` shortcut in `_roll_risk_period` (`goldbot/engine/runner.py:2129`);
      - the "resets" time on the entry-status card, `_next_day` (`goldbot/api/entry_status.py:235`);
      - `gate._risk_week`, Sunday 00:00 UTC (`goldbot/risk/gate.py:530`);
      - the supervisor follows the engines through their published `day_start_equity`
        (`goldbot/risk/supervisor.py:156`).
    - Risk today: an owner who sets `risk_day_anchor` in `settings.local.yaml` believes the caps' reset has moved, and
      nothing tells them it has not. If only `risk_day` honoured it, the reset would still happen at 00:00 UTC: the
      runner's `.date()` shortcut returns before `new_day` runs between the anchor and midnight. The card would also
      show the wrong "resets" time, and the perf report's drawdown would disagree with the gate.
    - **Recommendation.**
      1. Now (trading-safety review, BACKLOG 55): pin the setting. A Settings validator should refuse any value other
         than UTC 00:00 at load, saying the risk day is fixed. That only tightens, and it is safe on rollback (an older
         release ignores the key). `tests/test_report_days.py::test_the_configured_risk_day_anchor_is_the_hard_coded_risk_day`
         already pins the shipped yaml.
      2. Do not honour it piecemeal. Moving the risk day to the 22:00 UTC roll would stop one Asia session's losses
         being split across two caps at midnight. But it changes every daily-cap reset. It needs one shared
         risk-day function for all five places above, a migration for the persisted `_last_reset`, and an owner
         decision with a trading-safety review. Do it only if the quant reviewer finds the midnight split matters in
         measured trades.
    - A regression test pins today's behaviour: `new_day` does not reset at 22:30 UTC and does at 00:00.
  - Tests: tests/test_report_days.py (new, 17 cases). tests/test_monthly_report.py: 2 replaced, 3 new.
    tests/test_currency_followups.py: `account_month` now takes its first and last opportunity days as YYYY-MM-DD.
