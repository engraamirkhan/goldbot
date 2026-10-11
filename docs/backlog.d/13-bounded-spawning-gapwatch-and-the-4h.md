---
number: 13
rank: 16
title: Bounded spawning (`gap_watch`) and the 4h founder path
stage: Learning and adaptation · FIND
size: M–L
status: in review (gap_watch and the 4h retrain path done; 1d research-only)
---

### 13. Bounded spawning and the 4h founder path
Value: explores uncovered timeframes and regimes unattended, under the same gates, so the system keeps evolving
without a live winner to clone (FIND).
Acceptance: as in TRADER_LIFECYCLE section 3, with these tests passing:
- `tests/test_population.py::test_gap_founder_starts_in_shadow_with_zero_capital`;
- `::test_gap_founders_respect_the_monthly_and_reserved_slot_caps`;
- `tests/test_jobs_integration.py::test_no_spawn_during_system_halt`.

Also: `saturday_retrain` covers 4h, every spawn raises `n_pop`, and every spawn is logged with its gap id.

Status (2026-10-10): `gap_watch` and `Population.spawn_founder` are in review with the three tests above (G11).
4h founder path (2026-10-10): settings carry a 4h walk-forward window (48/6/6, purge 10 d, embargo 4 d), so
`saturday_retrain` trains 4h agents into challengers and gap_watch spawns 4h founders
(`tests/test_jobs_integration.py::test_a_4h_agent_retrains_into_a_challenger`,
`tests/test_gap_watch.py::test_a_4h_founder_is_spawned_for_an_uncovered_4h_timeframe_and_1d_stays_refused`); the
engine needed no change (`tests/test_engine.py::test_4h_agent_decides_only_on_4h_closes_and_its_shadow_trades_count_4h_bars`).
Still open: 1d. The engine keeps ~40 trading days of 1m bars (`EngineConfig.max_bars_in_memory`), short of the 120
daily bars a frame needs, so a daily agent would never decide; gap_watch keeps refusing 1d founders with a BACKLOG
suggestion until the engine holds daily history (an engine change, trading-safety review).

Rows: G3, G6, G11.
