---
number: 237
rank: 81
title: Refresh the exchange holiday calendar yearly and add early closes
stage: 'Research data · catalogue context'
size: S
status: 'proposed (follow-up of R145) · accountable: data-engineer · reviews: code-reviewer · due before 2026-12-31 (one year ahead of valid_through)'
---
- **237. Refresh the exchange holiday calendar yearly and add early closes.** Value: `config/research/exchange_holidays.yaml` is valid through 2027-12-31. A window beyond that silently has no holidays, and early closes (NYSE 13:00 ET half days, LSE 24/31 Dec) are not listed, although the S05 spec exits at the T-1 close with early closes honoured. Acceptance: a CI test fails when `valid_through` is less than a year away (like holidays_cme_metals.yaml). There is a yearly rebuild with the exchange_calendars version bumped and new ad hoc dates added to AD_HOC with their notice dates. Early closes are listed with their own available_utc, and S05 exits at the early close. The build's `--check` runs in review.
