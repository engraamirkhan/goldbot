- **The digest's "Yesterday" is the opportunity day (2026-10-16; BACKLOG 56, TRACEABILITY V10; code and ui-ux review
  pending).** Before this, the digest's proposals and trades lines covered the UTC calendar day, 00:00-24:00, while
  its month line used the opportunity day. A close at 23:00 UTC was in yesterday's trades and in today's month total.
  Both now use `goldbot/ops/report_days.py`: `day_before(now)` names yesterday and `day_bounds(day)` gives its
  [D-1 22:00, D 22:00) UTC instants. The digest does no day arithmetic of its own, and the month line is built from
  the same day.
  - **What the owner sees.** The header changes from "Yesterday (Thu 15 Oct, 00:00-24:00 UTC)" to
    "Thu 15 Oct trading day (ended 22:00 UTC, 23:00 UK)". The local time comes from `telegram.owner_tz` (default
    Europe/London, shown as "UK"; another zone shows its abbreviation) and is left out only if the zone cannot be
    resolved. With closes at Wed 22:30, Thu 10:00 and Thu 23:00 UTC, the Friday digest
    used to show "Closed 2 trades: net +$30.00 · +0.60R (1 won, 1 lost)" next to "October so far: ... 2 trades ·
    +1.50R". It now shows "Closed 2 trades: net +$80.00 · +1.50R (2 won, 0 lost)", the same trades as the month line.
    The Thu 23:00 close moves to Saturday's digest.
  - **Weekends.** Friday's opportunity day ends at 22:00 UTC, at or after the Friday close in both US DST regimes, so
    Saturday's digest reports Friday. Sunday's day, Sat 22:00 to Sun 22:00 UTC, holds no session: the reopen (22:00
    UTC in summer, 23:00 in winter) opens Monday's day. So Monday's digest says "Trades closed: none" for Sunday, and
    the reopen's closes arrive on Tuesday. Nothing is counted twice or dropped, and Friday is not repeated on Monday.
    A Saturday or Sunday with no proposal and no close shows one line instead of the two "none" lines: "Weekend:
    market closed, no trading. Sunday-evening trades show in Tuesday's digest." A weekend day that has rows (a winter
    Friday close stamped exactly 22:00 UTC is Saturday's) shows them as usual.
  - **Late sends (code review).** "Yesterday" is worked out from the scheduled slot (`DigestSchedule.slot`, passed
    by the bot as `build_digest(as_of=...)`), not from the send time. With `digest_at` 18:00, a send 4.5 h late at
    22:30 used to report the day just ended and the next day's digest reported it again, losing the day before. The
    first line and the expiry of pending proposals still use the send time. The month line now gets the day itself
    (`build_monthly_report(day=...)`). Tested in a summer week, the week US
    DST ends (1 Nov 2026) and a winter week.
  - **Not changed.** The risk line (drawdown, day and week loss against the caps) reads the engine's figures, which
    reset on the 00:00 UTC risk day. No limit, RiskGate check or engine code was touched. The digest has no day input
    to the risk line, so no digest test can catch it moving; the reset itself is pinned by
    `tests/test_report_days.py::test_the_risk_day_still_resets_the_day_start_equity_at_00_utc_not_at_the_22_utc_roll`.
  - Also: `goldbot/research/min_lot.py` `--size-down` help said "8% stage", which argparse read as a "% s" format and
    printed the action's attribute dict; now "8%%", with a test that `--help` renders.
  - Tests: tests/test_digest.py 10 new (12 cases), 1 updated; tests/test_report_days.py 2 new (5 cases);
    tests/test_min_lot.py 1 new. RUNBOOK 0.5 "Morning digest" updated.
