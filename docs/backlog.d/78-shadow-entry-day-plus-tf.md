---
number: 78
rank: 15
title: Shadow-book day clustering keys on entry_ts + tf (ADR 0022 correction)
stage: Research (attribution, ADR 0022) · LEARN
size: S
status: 'done in 891dde1 (code already keyed on entry_ts + tf; ADR 0022 note and tests pinned 2026-10-17) · awaiting quant-reviewer · due 2026-11-06'
---
- **78. Shadow-book day clustering keys on entry_ts + tf (ADR 0022 correction).** Value: ADR 0022 says day clustering uses the entry UTC day and live trades use the fill day, but the shadow book keys on the signal bar's OPEN (`labels/excursion.py` `entry_ts`), so the last bar of a UTC day lands on the wrong day; E-6 (BACKLOG 76) already uses `entry_ts + tf` (`pipeline.entry_utc`), as `attribution.py:396` does. Acceptance: shadow-book clustering keys on `entry_ts + tf`; a dated note in ADR 0022 says this corrects the code to match its rule, not a rule change; a test with a 15m 23:45 signal lands on the next UTC day; frozen Q4 readings unchanged (documented delta if any CR1 bar moves).
