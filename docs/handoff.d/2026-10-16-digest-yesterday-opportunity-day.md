- **The digest's "Yesterday" is the opportunity day (2026-10-16; BACKLOG 56, TRACEABILITY V10; code and ui-ux review
  pending).** Before this, the digest's proposals and trades lines covered the UTC calendar day, 00:00-24:00, while
  its month line used the opportunity day. A close at 23:00 UTC was in yesterday's trades and in today's month total.
  Both now use `goldbot/ops/report_days.py`: `day_before(now)` names yesterday and `day_bounds(day)` gives its
  [D-1 22:00, D 22:00) UTC instants. The digest does no day arithmetic of its own, and the month line is built from
  the same day.
  - **What the owner sees.** The header changes from "Yesterday (Thu 15 Oct, 00:00-24:00 UTC)" to
    "Yesterday (to 22:00 UTC) · Thu 15 Oct". With closes at Wed 22:30, Thu 10:00 and Thu 23:00 UTC, the Friday digest
    used to show "Closed 2 trades: net +$30.00 · +0.60R (1 won, 1 lost)" next to "October so far: ... 2 trades ·
    +1.50R". It now shows "Closed 2 trades: net +$80.00 · +1.50R (2 won, 0 lost)", the same trades as the month line.
    The Thu 23:00 close moves to Saturday's digest.
  - **Weekends.** Friday's opportunity day ends at 22:00 UTC, at or after the Friday close in both US DST regimes, so
    Saturday's digest reports Friday. Sunday's day, Sat 22:00 to Sun 22:00 UTC, holds no session: the reopen (22:00
    UTC in summer, 23:00 in winter) opens Monday's day. So Monday's digest says "Trades closed: none" for Sunday, and
    the reopen's closes arrive on Tuesday. Nothing is counted twice or dropped. Tested in a summer week, the week US
    DST ends (1 Nov 2026) and a winter week.
  - **Not changed.** The risk line (drawdown, day and week loss against the caps) reads the engine's figures, which
    reset on the 00:00 UTC risk day. No limit, RiskGate check or engine code was touched. A digest sent at 22:00 UTC or
    later (only if `telegram.digest_at` is set that late) already reports the day just ended.
  - Also: `goldbot/research/min_lot.py` `--size-down` help said "8% stage", which argparse read as a "% s" format and
    printed the action's attribute dict; now "8%%", with a test that `--help` renders.
  - Tests: tests/test_digest.py 6 new (8 cases), 1 updated; tests/test_report_days.py 2 new (5 cases);
    tests/test_min_lot.py 1 new. RUNBOOK 0.5 "Morning digest" updated.
