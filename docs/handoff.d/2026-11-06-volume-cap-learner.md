- Volume cap and learner (branch `fix/volume-cap-learner`, PD 2026-11-06; measured 830-860 demo fills a day against
  the owner's 150-250, 69% random controls; review round: quant Q1-Q9, trading-safety LOW-1/2, code review LOW-1):
  - Daily entry budget (accounts.yaml `exploration.daily`, `ops.accounts.DailyEntries`; engine
    `engine/exploration_budget.py`): icm-probe 85 / 8 an hour, icm-demo 70 / 7, vantage-demo 40 / 4 exploration
    fills, counted by signal close (aux migrations 23-24 `signal_ns`, backfilled; index on account, signal time, arm)
    from the UTC day's start, every book together; each session (asia / london / newyork) gets its trailing-10-day
    share of the day. A paired control is admitted per pair by matched-time Bernoulli thinning (p = min(1, its
    arms' fills / its signals in the same session x hour cell, trailing 10 trading days), seeded hash_u, p recorded
    as the propensity); unpaired per-instrument controls (S0-<key>, I0-<key>) take at most 0.25 of the day and stop
    once their instrument's cost cell has 30 fills (counts cached a minute, only rising)
    (docs/research/prereg-amendment-2026-11-06-control-thinning.md). Refusals: `exploration_refused:daily_entries|
    hourly_entries|session_entries|control_thinned|thin_controls|thin_cell_complete`, candidates recorded blocked.
    Entries only: exits are never counted or gated; the RiskGate is still the only path to an order. Rollback: drop
    `daily:` and restart.
  - Matched reads (`ops/perf_report.py` `_excess`) report the share of the arm's fills in cells with no control fill;
    above 10% the read is "not readable".
  - Control stops and retirements (PD+PO 2026-11-06, kill list; A5 signed by the quant-reviewer,
    docs/research/a5-signoff-2026-11-06.md). Spec rows retired 2026-11-06 (unhashed `retired_day`/`retired_rule`
    only, K unchanged): BXR/BXT/BXF 15/30/1H/4H and XBB15 (A2); the unpaired controls I0, VI0, C1H, VC1H, W0, VW0; A5:
    S5A, S5B, Z4D, Z4P, Z6D, Z6V, Z7D, Z7P, Z7V with their LC* controls; ZCV (its vantage arms gone). ZCD stays (Z3D,
    Z9D, Z10D). Open positions keep every exit. icm-demo's swing pot has no active arm.
  - Learner (`exploration_data.learn`, `entry_exclusions`): retired arms (spec row from its `retired_day`, or the
    broker's `exploration_arm_status` from its `recorded_ns`: point in time), blocked or unqualified arms and arms not
    configured on the broker are written at weight 0 (`detail.excluded`); an enterable arm with no labelled fill holds
    the cold-start floor min(FLOOR_SHARE, 1/n) (`detail.cold_start`); the rest renormalise. `weights` reads a 0 as
    "cannot enter" instead of falling back to equal shares.
  - Cost probes (CFO/finance 2026-11-06, -£107 in 12 days): icm-probe scheduled every_min 60 -> 120 (every ICM session
    is past PROBE_MIN_FILLS 50). Vantage NOT halved: its london (43) and newyork (47) scheduled fills are under it.
    `probe_plan` ICM 30 -> 15; trades_view targets now 100 / 70 / 80 (budget + probes). Health `costs:of_record` WARNs
    when the cost of record's cells (ICM, Vantage) were last measured more than 30 days ago.
