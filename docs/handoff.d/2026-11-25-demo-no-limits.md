- **Demo accounts without limits, made safe (ADR 0097; owner 2026-11-25 "allow absolutely anything"). The flag is
  false everywhere until the icm-probe canary.**
  - **Switch.** `demo_unlimited` in config/accounts.yaml applies to demo probe_only accounts only. It lifts every
    account-configured cap in the RiskGate and in exploration.
  - **What stays.**
    - The RiskGate path, the broker stop on every order, halts, integrity checks, rollover and the weekend.
    - Statistical rules: the frozen spec's `max_open`, a learning arm's registered loss cap and `until`, prereg entry
      refusals, H-81 cost suspension and retirements.
  - **Guards.**
    - The demo floor: equity at least 20 % of the mark, margin at least 200 % on the broker's figure.
    - Health warns at 50 % of the mark.
    - Per-arm runaway containment, `arm_runaway`: one arm's day stays within 10 % of the mark.
  - **Exits before entries (trading-safety H1/H2), engine/order_send.py.**
    - No entry while any close, kill flatten or due stop modify is pending.
    - Entry duty ceiling: 1.5 s of entries a pass, half of every 10 s.
    - A 10033/10040 broker refusal holds entries until a position closes.
    - The loop-pass gap p99 is published, and health `exit_latency:<account>` alerts above 5 s.
    - Capacity ceiling: 300 open positions, the load at which the exit SLO test passes on the paper bridge.
  - **Evidence tag.** Every entry carries `limits_epoch=unlimited`. Shock-window fills are tagged and lifted (M1).
  - **Live unchanged.** `engine_limits` of every non-probe account is pinned to origin/main (fixture test).
    `RiskGate.update_stage`, the owner re-arm path, is byte-identical; the freeze lives in `stage_step`.
  - **Next.**
    - Set `demo_unlimited: true` on icm-probe at the canary (Sun 2026-11-29 22:00 UTC) and read it 24 h later
      (RUNBOOK "Demo accounts without limits").
    - The other demo accounts follow after W2-4.
    - config.py/settings.yaml (`risk.demo_floor`) rebase on W1-B.
