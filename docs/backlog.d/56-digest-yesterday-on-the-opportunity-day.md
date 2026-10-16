---
number: 56
rank: 48
title: Digest Yesterday line on the opportunity day
stage: Performance review · RUN
size: S
status: proposed (quant review Q1 of the north-star day lane, 2026-10-16)
---
- **56. Digest "Yesterday" line on the opportunity day.** Value: the digest's "Yesterday" proposals and trades
  lines still run 00:00-24:00 UTC (`goldbot/telegram/digest.py` `build_digest`). Its month line and the monthly and
  performance reports use the opportunity day (`goldbot/ops/report_days.py`). So a close at 23:00 UTC is in
  yesterday's trades line and in today's month line. Acceptance:
  - for a digest built on day D, "Yesterday" covers [D-2 22:00, D-1 22:00) UTC, the opportunity day D-1, through
    `report_days` (no new day arithmetic in the digest);
  - the header reads "Yesterday (to 22:00 UTC)" with the weekday and date of D-1;
  - tests at 21:59 and 22:00 UTC, on a Monday digest (the Sunday reopen counts on Monday), and in a US DST week;
  - the risk lines (loss-cap use) stay on the risk day, 00:00 UTC; no limit changes;
  - reviews: code, ui-ux (Telegram wording).
