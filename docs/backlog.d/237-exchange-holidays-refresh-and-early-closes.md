---
number: 237
rank: 81
title: Refresh the exchange holiday calendar yearly and add early closes
stage: 'Research data · catalogue context'
size: S
status: 'data side done 2026-11-14 (branch fix/data-backlog-1114); S05 exit -> BACKLOG 258 · accountable: data-engineer · reviews: code-reviewer'
---
- **237. Refresh the exchange holiday calendar yearly and add early closes.** Value: `config/research/exchange_holidays.yaml` is valid through 2027-12-31. A window beyond that silently has no holidays, and early closes (NYSE 13:00 ET half days, LSE 24/31 Dec) are not listed, although the S05 spec exits at the T-1 close with early closes honoured. Acceptance: a CI test fails when `valid_through` is less than a year away (like holidays_cme_metals.yaml). There is a yearly rebuild with the exchange_calendars version bumped and new ad hoc dates added to AD_HOC with their notice dates. Early closes are listed with their own available_utc, and S05 exits at the early close. The build's `--check` runs in review.
  **Data side done 2026-11-14.** r2 of config/research/exchange_holidays.yaml: exchange_calendars 4.13.2 (still the latest on PyPI, so no bump), valid through 2028-12-31, each exchange's `early_closes` (close_utc and available_utc; NYSE 1997-12-26, 1999-12-31, 2003-12-26 ad hoc early closes knowable the day after: notices not verified). `exchange_holidays.early_closes(ex, known_by_ns=)` reads and validates them. tests/test_exchange_holidays_refresh.py fails once valid_through is under a year away; the yearly steps are in the generator's docstring; `--check` passes. The calendar's sha256 changes: catalogue harness hashes change (BACKLOG 236). S05 exiting at the early close is BACKLOG 258.
