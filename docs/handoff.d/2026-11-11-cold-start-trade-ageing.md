- 2026-11-11 (branch fix/cold-start-trade-ageing; backlog 160, R124): open trades age from every base bar close after
  any cold start. Before, `on_bar_close` returned at its 400-bar floor before `_manage_open`, so after a restart with no
  stored history an open trade's `bars_held` paused until the first decision frame (~30 h for 15m arms) and its time
  exit fired that late (broker SL/TP and tick stops still ran). Now the time exit fires at its bar; `bars_held` is
  saved in the orders file, so a restart resumes the count (bars missed while the engine was down are still not
  counted: backlog 161). Below the floor a policy's hard flat also fires at the first base close at or after its
  deadline (review C1) and the orders file is saved at that close (C2); the trail still waits for its frame
  (engine/below_floor.py). The replay golden was regenerated:
  the replay starts cold, so 6 RB15 trades of 2025-03-03 now time out after 4 bars instead of riding to stop/target;
  the freed slots move 7 entries in on 03-03; 11 RB15 entries of 03-04 go (`arm_budget`: the arm's daily loss room)
  and 5 later ones go (`min_lot_exceeds_risk`: lower equity). Ledger legs of trades opened in a no-history cold start
  before R124 may show stop/target where the arm's hold was a time exit; the performance-analyst ledger-vs-label
  reconciliation excludes or flags them.
  Tests: tests/test_engine_cold_start_ageing.py.
