---
number: 163
title: A bar re-closed after a restart can re-run a refused entry at a later price
stage: 'engine (trading-safety review of backlog 161, item 5, 2026-11-12)'
size: S
status: 'done (triage 2026-11-13): PR #377 (R129)'
---
- **163. A bar re-closed after a restart can re-run a refused entry at a later price.** Value: `_closed_through` is in memory only, so a restart whose first quote is stamped before a close the previous engine already processed closes that bar again and runs its decisions again (backlog 161 only skips its bar count). A signal RiskGate refused at the original close (spread, heat, budget) can then pass at a later price: a stale entry, not a duplicate (sent ids hold), and still gated and, on real money, approved. Acceptance, tests first: the last processed base close is persisted per book with the orders file and restored at start, so a restart never decides a bar twice; a test restarts with a quote stamped before the last processed close and sees no decision for that bar; the late-tick accounting (`_late_tick`, `late_ticks` block) is checked for a restart's stale quote (one stale quote never blocks entries); old files without the value start as today.
