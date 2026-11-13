---
number: 16
title: Swap in the live EV check (playbook G-1)
stage: Capital management, costs · RUN
size: S
status: 'done (triage 2026-11-13): swap is in the daily projection (BACKLOG 36) and measured per swap mode, converted by the gate (PR #359, #379)'
---
- **16 (G-1).** The gate's EV adds expected swap in ATR (side, nights implied by `max_bars`, triple Wednesday).
  Test: a 4h long whose EV is positive before swap and negative after is refused; shorts are unchanged.
  Sweep 2026-10-16 (product owner): Parked: at £1,000 only 15m trades fit, so overnight swap barely touches the EV; revisit at the 1h/4h milestone.
