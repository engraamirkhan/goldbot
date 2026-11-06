- Volume cap and learner (branch `fix/volume-cap-learner`, PD 2026-11-06; measured 830-860 demo fills a day against
  the owner's 150-250, 69% random controls):
  - Daily entry budget (accounts.yaml `exploration.daily`, `ops.accounts.DailyEntries`; engine
    `exploration.daily_refusal`): icm-probe 85 / 8 an hour, icm-demo 70 / 7, vantage-demo 40 / 4 exploration fills,
    counted from the ledger since the day reset (a restart never resets it), every book together. A random control
    fills only while it stays at most one per strategy/learning fill; the unpaired per-instrument controls (S0-<key>,
    I0-<key>) take at most 0.25 of that, so gold's controls and the learning twins pair first
    (docs/research/prereg-amendment-2026-11-06-control-thinning.md; K unchanged, no arm row edited). Refusals are
    journalled `exploration_refused:daily_entries|hourly_entries|control_ratio|thin_controls` and recorded as blocked
    candidates. Entries only: exits are never counted or gated; the RiskGate is still the only path to an order.
    Rollback: drop `daily:` and restart.
  - Control stops and retirements (PD+PO 2026-11-06, kill list; A5 signed by the quant-reviewer,
    docs/research/a5-signoff-2026-11-06.md): an unpaired per-instrument control stops for good at 30 fills on its
    account (`thin_cell_complete`, `DailyEntries.thin_control_fills`). Spec rows retired 2026-11-06 (unhashed
    `retired_day`/`retired_rule` only, K unchanged): BXR/BXT/BXF 15/30/1H/4H and XBB15 (A2); the unpaired controls
    I0, VI0, C1H, VC1H, W0, VW0; A5: S5A, S5B, Z4D, Z4P, Z6D, Z6V, Z7D, Z7P, Z7V with their LC* controls; ZCV (its
    vantage arms gone). ZCD stays (Z3D, Z9D, Z10D). Open positions keep every exit.
  - Learner (`exploration_data.learn`, `entry_exclusions`): retired arms (spec row or the broker's
    `exploration_arm_status`), blocked or unqualified arms, arms with no labelled fill and arms not configured on the
    broker are written at weight 0 (`detail.excluded`); shares renormalise over arms that can enter. `weights` reads a
    0 as "cannot enter" instead of falling back to equal shares.
  - Cost probes (CFO/finance 2026-11-06, -£107 in 12 days): icm-probe scheduled every_min 60 -> 120 (every ICM session
    is past PROBE_MIN_FILLS 50). Vantage NOT halved: its london (43) and newyork (47) scheduled fills are under it.
    `probe_plan` ICM 30 -> 15; trades_view targets now 100 / 70 / 80 (budget + probes).
