---
number: 149
rank: 57
title: Exchange holidays from a published calendar, not missing data days
stage: 'Research data · catalogue context'
size: M
status: 'proposed · accountable: data-engineer · reviews: quant-reviewer, code-reviewer'
---
- **149. Exchange holidays from a published calendar, not missing data days.** Value: `dispatcher.build_ctx` builds `holiday_<exchange>` as weekdays with no bar in the long daily series over the whole window, so a holiday is inferred from future missing data (a data gap reads as a holiday, and the set is known only after the fact). S05 (pre-holiday) and every detector reading `ctx.events` depend on it; Lane 4's S6 keeps scheduled events whole because ctx carries no publication time (quant round 2). Acceptance: holidays from a published exchange calendar (NYSE/LSE/Xetra), versioned, each date with an `available_utc` at or before the decision that uses it; ctx carries that availability so S6 cuts events by it (`truncate_ctx(..., scheduled_events=False)` then passes on S05-S07); a test where a data gap that is not a holiday is not a holiday; explore-window S05 events before/after counts reported (a changed event set is a new registry row).
