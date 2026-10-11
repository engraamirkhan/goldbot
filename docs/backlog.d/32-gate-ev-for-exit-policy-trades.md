---
number: 32
rank: 32
title: Gate EV for exit-policy trades uses the measured mean winner (G-12)
stage: Capital management · RUN
size: S
status: proposed (trading-safety review)
---
- **32. Gate EV for exit-policy trades (G-12; trading-safety review).** Value: the gate's EV
  `p × target_atr − (1 − p) × stop_atr − cost` (`gate.py:242`) assumes every win reaches the target, but under a
  policy `p` is P(win) (`exit_policy.policy_label`) (RUN). Acceptance:
  - `Intent` gains `win_atr`: the agent's measured mean winner in ATR from its labels, capped at `target_atr`.
    EV uses `min(win_atr, target_atr)`.
  - Without a policy, `win_atr = target_atr` and decisions are identical (test).
  - A test where a capped runner with p = 0.45, cap 10 ATR and mean winner 2.0 ATR is refused when the EV is
    negative.
  - The change never allows a trade today's gate refuses (property test over a grid).
  - Depends on: 31.
