- **W1-1 bridge smoke built (BACKLOG 1, ADR 0021, plan docs/proposals/2026-10-w1-1-bridge-smoke-plan.md; implementer,
  2026-10-16, eb52fad..ed14a83; not yet reviewed).** `goldbot run bridge-smoke icm-demo` prints the masked login,
  server, balance and a tick, then buys and closes the minimum lot after one owner click on the ApprovalBus (no click
  in 90 s: exit 3, nothing sent). `RiskGate.check_operational` shares `check`'s account checks and every limit after
  sizing, and skips only the five named model checks. `check` was split into shared helpers as a pure refactor: a
  720-case decision grid captured before the split stays identical (tests/test_risk_gate_split.py).
  Fences: F3 is an AST test (only ops/smoke.py references `check_operational`). F4 is in GuardedBroker: magic + 99 only
  on a terminal that reports demo, with the protocol digest unchanged. The smoke needs the engine unit inactive, its
  heartbeat at least 150 s old and a non-blocking flock (0600); `smoke_state` sets every AccountState field from a
  named source. The ledger lists smoke positions apart from RC-8. Closes are never gated. The smoke itself has **not**
  been run against a server. Order of work for the
  demo run: update the MT5 box first (F4), then RUNBOOK 0.4 "Bridge smoke". Reviews due: trading-safety (gate split,
  order/close path), security (masked login, lock mode), SRE (RUNBOOK steps).
