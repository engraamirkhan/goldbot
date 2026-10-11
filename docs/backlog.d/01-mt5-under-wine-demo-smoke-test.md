---
number: 1
rank: 4
title: MT5-under-Wine demo smoke test on Oracle
stage: Operational resilience, execution · RUN
size: S
status: ready
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
