---
number: 1
rank: 1
title: MT5-under-Wine demo smoke test on Oracle
stage: Operational resilience, execution · RUN
size: S
status: 'in progress (`lane/claude-run-deploy`, plan ccef822) · accountable: release-manager · due 2026-10-18 (re-dated from 2026-10-13: the demo deploy never ran) · LATE'
---
### 1. MT5-under-Wine demo smoke test on Oracle
Value: until a real terminal has run, no cost, fill or reconciliation number in the system is measured. Every later
item rests on them (RUN).
Acceptance:
- A `run.py bridge-smoke` command, with a unit test against the paper broker, connects through the bridge. It
  prints account login, server, balance and a live XAUUSD tick, then opens and closes 0.01 lot on a demo account. It
  exits non-zero if the account is not classified as demo.
- On the Oracle VM, `run.py health` reports the engine, bridge, supervisor and scheduler as running for 24 h with
  no stale-engine entry. The output is pasted on issue #34.
- `state/costs_<account>.json` carries a measured spread and at least one measured swap after the first rollover.

Depends on: owner steps (VM provisioned, demo credentials in the keyring). Rows: X6–X10 unverified on real
hardware; lifecycle "Operational resilience".

Sweep 2026-10-16 (product owner): accountable `release-manager` (sre runs the box), due 2026-10-18, re-dated from 2026-10-13 because the deploy was not run or chased for three days (scorecard 2026-W42). Acceptance unchanged (above); the deploy uses a SHA that includes #78, and the 24 h health output goes on #34. The `run.py bridge-smoke` command is ROADMAP W1-1.
