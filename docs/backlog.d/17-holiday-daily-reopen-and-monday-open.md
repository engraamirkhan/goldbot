---
number: 17
rank: 20
title: Holiday, daily-reopen and Monday-open entry rules (G-2)
stage: Operational risk · RUN
size: S–M
status: 'proposed (owner: windows)'
---
- **17 (G-2).** Holiday calendar (US, UK, JP, CN, 24 Dec–2 Jan, early closes): no entries on those days. No
  entries for 15 min after each daily reopen and 30 min after the weekly open. Fake-clock tests. Exits are untouched.
  **Built 2026-10-11, off by default (TRACEABILITY R36):** `risk.reopen: {enabled: false, after_daily_reopen_min: 10,
  after_monday_open_min: 30}` from the broker session table (DST per the server clock; a reopen after 12 h or more
  closed takes the weekly window) and `risk.holidays: {enabled: false}` from the versioned list
  `config/holidays_cme_metals.yaml` (US federal holidays as a proxy, not measured, less Columbus and Veterans Day;
  Good Friday, Easter Monday, the day after Thanksgiving, 24/26/31 Dec, 1/2 Jan; 2026-2028; by server date = CME
  trading day; missing or expired = `calendar_unavailable`; measured before adoption, item 45; replay, item 46).
  Reasons `reopen_window`, `holiday_thin`.
  Not covered: UK, JP and CN holidays and early closes (the list is US/CME only; a broker-reported short session
  already blocks as `market_closed`). Owner: adopt and set the minutes (10 vs the playbook's 15). Needs
  trading-safety review.
