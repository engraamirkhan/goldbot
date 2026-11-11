- 2026-11-11 (branch fix/cold-start-trade-ageing; backlog 160, R124): open trades age from every base bar close after
  any cold start. Before, `on_bar_close` returned at its 400-bar floor before `_manage_open`, so after a restart with no
  stored history an open trade's `bars_held` paused until the first decision frame (~30 h for 15m arms) and its time
  exit fired that late (broker SL/TP and tick stops still ran). Now the time exit fires at its bar; `bars_held` is
  saved in the orders file, so a restart resumes the count (bars missed while the engine was down are still not
  counted). Exit policies (trail, hard flat) still wait for their timeframe's frame. The replay golden was regenerated:
  the replay starts cold, so 6 RB15 trades of 2025-03-03 now time out after 4 bars instead of riding to stop/target;
  the freed slots, the arm's daily loss room and equity then move later entries (7 added on 03-03, 16 gone after).
  Tests: tests/test_engine_cold_start_ageing.py.
