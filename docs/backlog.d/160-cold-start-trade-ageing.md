---
number: 160
title: Open trades age from the first live bar after any cold start
stage: 'engine (found while fixing backlog 155, 2026-11-11)'
size: S
status: 'done (triage 2026-11-13): PR #372 (R124)'
---
- **160. Open trades age from the first live bar after any cold start.** Value: on a cold start `on_bar_close` returns at its 400-bar floor before `_manage_open`, so an open trade's `bars_held` does not advance until the first decision frame exists (about 30 h for 15m arms). Time exits fire that much late after a restart with open positions; broker SL/TP and the per-tick stop checks still run. The replay golden (tests/test_engine_replay_identity.py) encodes this, so a fix changes replays and needs a deliberate golden update. Backlog 155 runs `_manage_open` below the floor only while a failed or short history read holds entries. Acceptance, tests first: `bars_held` counts on the base clock from the position's open (or the restart's first live bar) on every cold start; the replay golden is regenerated with the exact deals that move listed and a quant sign-off that no research reading depends on the old timing; trading-safety confirms exits only get earlier, never later.
