---
number: 165
title: Replay the bars missed while down for open shadow trades at warm start
stage: 'engine / research (quant review of backlog 161, Q-1, 2026-11-12)'
size: M
status: 'done (triage 2026-11-13): PR #377 (R129)'
---
- **165. Replay the bars missed while down for open shadow trades at warm start.** Value: the shadow book advances only on the bars the engine closes (`ShadowBook.on_bar`), so a shadow trade open across a restart misses every bar that closed while the engine was down: its bar count, barriers, policy and excursion skip that span. Backlog 161 tags such trades `restart_gap` (persisted) so they can be excluded; the evidence is lost, not repaired, and every deploy loses some. Acceptance, tests first: once the warm-start history covers the downtime (the background fill done), each open shadow trade is advanced through the missed bars of its own timeframe in order, exactly as `on_bar` would have (a test: a trade replayed across a restart ends identical to one that never restarted); a trade whose missed bars the history does not fully cover keeps `restart_gap`; the calibration and shadow-vs-live reads (promotion PerfStats, the research director's shadow_z, `_shadow_win`) exclude `restart_gap` trades until then.
