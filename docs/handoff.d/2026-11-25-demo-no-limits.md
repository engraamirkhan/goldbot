- **Demo accounts without limits, made safe (ADR 0097; owner 2026-11-25 "allow absolutely anything"). The flag is
  false everywhere until the icm-probe canary.**
  - **Switch.** `demo_unlimited` in config/accounts.yaml applies only to a demo probe_only account that has the
    order lane or `order_timeout_s` <= 15 s; Vantage cannot take it as configured. It lifts every
    account-configured cap in the RiskGate and in exploration.
  - **What stays.**
    - The RiskGate path, the broker stop on every order, halts (a HALTED stage stays until /rearm), integrity
      checks, rollover and the weekend.
    - Statistical rules: the frozen spec's `max_open`, a learning arm's registered loss cap and `until`, prereg
      entry refusals, H-81 cost suspension and retirements.
  - **What is lifted.** The drawdown kill switch no longer flattens there (trading-safety M2, owner information).
  - **Guards.**
    - The demo floor: equity at least 20 % of the mark, margin at least 200 % on the broker's figure.
    - Health warns at 50 % of the mark.
    - `arm_runaway` (exploration arms): one arm's day stays within 10 % of the mark.
  - **Exits before entries (trading-safety H1/H2), engine/order_send.py.**
    - No entry while a close (outside its back-off) or a kill flatten is pending.
    - Entry duty ceiling: 1.5 s of entries a pass, half of every 10 s.
    - A 10033/10040 refusal holds entries for at least 60 s, then until a position closes.
    - The 15-minute pass-gap p99 is published, and health `exit_latency` judges unlimited accounts only (5 s on
      one book, 6.5 s on several).
    - Capacity ceiling: 300 open positions, measured.
  - **Evidence (quant review of #454; ADR 0097 §7, reads (a)-(e)).**
    - Every entry carries `limits_epoch=unlimited` and `capped_reasons`, on exploration, probe, journal and
      closed-trade records.
    - `evidence_exclusions.unlimited_market_state` keeps shock and wide-spread fills out of every cost, stop,
      edge-cap and kill read.
    - Rankings and perf_report leave unlimited rows out and show them on their own line.
    - Cohort amendment: docs/research/prereg-amendment-2026-11-26-cohort-unlimited.md (the weekly cap is judged in
      shadow). GD-1 disclosure added. ADR 0098 calibrates per epoch.
  - **Live unchanged.** `engine_limits` of every non-probe account is pinned to origin/main (fixture test), and
    `update_stage` is byte-identical.
  - **Next.**
    - Set the flag on icm-probe at the canary (Sun 2026-11-29 22:00 UTC) and read it 24 h later (RUNBOOK).
    - The other demo accounts follow after W2-4.
