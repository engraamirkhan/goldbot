# Plan: W1-1 bridge smoke (BACKLOG 1)

Status: plan, architect (lane A-PLAN), 2026-10-16. Checked against docs/DESIGN.md and docs/TRACEABILITY.md
(rows X6–X10 untested on real hardware). Build Fri 10-23; demo run Mon 10-26. Needs ADR 0020 (proposed) reviewed by
the trading-safety reviewer before the build.

**Requirement.** BACKLOG 1: "`run.py bridge-smoke` … prints account login, server, balance and a live XAUUSD tick,
then opens and closes 0.01 lot on a demo account; exits non-zero if the account is not classified as demo." Design:
RiskGate is the only path to an order; exits are never gated.

## Decisions (record on a new TRACEABILITY row)

1. **Order path.** A new `RiskGate.check_operational` runs every account check and limit and skips only the model
   checks (`risk/gate.py:451-465`: expected value, target over cost, time exit), which would otherwise need an invented
   win probability. Size is exactly `volume_min`. Proposed as ADR 0020.
2. **Public output.** The login prints masked (`****123`): the output is posted on the public issue #34.
3. **Smoke magic** `magic_base+99`: inside `GuardedBroker`'s accepted range (`bridge.py:256`); no family uses it
   (families land on 61, 62, 81, 82, 92; `runner.py:2995`).
4. **Persistence.** No state file. The record is the broker's deal history plus the command output.

## Files

| File | Change |
|---|---|
| new `goldbot/ops/smoke.py` | `run_smoke(broker, acc, state_dir, *, now, place=True) -> SmokeReport`, `main(argv) -> int`, `SMOKE_MAGIC_OFFSET = 99` |
| `ops/commands/ops.py:28` | `@register_command("bridge-smoke", pos=210)` |
| `ops/run.py:13` | one usage line |
| `execution/mt5_adapter.py:199` | `timeout=MT5_INIT_TIMEOUT_MS` (60 000) — being added by the SRE lane on 2026-10-16 |
| `risk/gate.py:391-544` | account and limit checks into shared helpers; add `check_operational` |
| `ops/ledger.py:303` | `broker_trades` lists smoke positions separately (no false RC-8 mismatch); coordinate with W1-4 (data-engineer) |

Must not change: the bridge protocol (`METHODS`, `API_VERSION`; bridge stays v5), `GuardedBroker`, `runner.py`, the
records in `broker.py`.

## Data flow (on the brain)

1. `bridge_endpoint` (`accounts.py:221`) → `RemoteBroker` → `check_bridge` (`bridge_compat.py:256`).
2. `account()`, then `verify_terminal_account` and require `acc.mode == "demo"`; either failing exits 2.
3. Print `symbol_info` and `last_tick`.
4. Pre-order checks, any failing exits 4: the engine loop heartbeat (`watchdog.py:44`) is ≥ `LOOP_FAIL_S` (150 s) old;
   the bridge is compatible; the engine's risk state is readable.
5. `AccountState` from the live reads, `risk_<acc>.json` (read only), `ApprovalBus.control()` and
   `engine_should_halt` (`supervisor.py:233`).
6. `check_operational`; a refusal exits 3.
7. Place with server-side stop and target, then close, retrying up to 3 times.

**Fail closed.** A refused order exits 3. `BridgeUnavailable` after the send: find the position by magic and comment,
close it, exit 3. A close still failing after the retries exits 5 "LEFT OPEN"; the server-side stop stays and engine
reconciliation (X8) adopts the position on its next start. A leftover smoke position is closed first.

## Failing tests first

`tests/test_smoke.py` (against `PaperBroker` with a demo `AccountInfo`):
`test_prints_masked_login_server_balance_and_tick_then_opens_and_closes_the_minimum_lot`,
`test_full_login_never_appears_in_the_output`, `test_exits_non_zero_when_the_terminal_is_not_demo`,
`test_refuses_a_live_account_even_when_unlocked`, `test_an_owner_halt_refuses_and_sends_nothing`,
`test_a_refused_order_exits_non_zero_and_leaves_no_position`, `test_a_timeout_after_send_finds_and_closes_the_position`,
`test_a_failed_close_is_retried_then_reported_left_open`, `test_engine_heartbeat_149_s_old_refuses_and_150_s_allows`,
`test_missing_or_corrupt_risk_state_refuses_the_order_but_reports_the_reads`,
`test_a_leftover_smoke_position_is_closed_first`, `test_smoke_magic_is_guarded_and_no_family_uses_it`.

Also: `tests/test_risk_gate_split.py::test_check_decisions_unchanged_on_a_state_grid`,
`::test_operational_check_applies_every_account_limit_and_no_model_check` (a daily loss exactly at the limit is allowed),
`tests/test_mt5_init.py::test_initialize_gets_the_terminal_path_and_a_60_s_timeout`,
`tests/test_ledger.py::test_smoke_positions_are_listed_not_rc8_mismatches`; `tests/test_ops_run_registry.py` pins the
new command.

## Risks

| Risk | Test |
|---|---|
| The gate split changes engine decisions | decisions compared on a grid of states |
| Smoke and engine both trading | heartbeat boundary test; RUNBOOK: stop `engine@icm-demo` first |
| Duplicate order after a timeout | no retry on orders (`bridge.py:352`); lookup test |
| Login leaks on #34 | masked-output test |
| Attach hangs under Wine | timeout test; the real proof is the 10-26 run |

**Reviews.** Trading-safety: the gate split, ADR 0020, the order and close paths. Security: output masking, token
handling. SRE: RUNBOOK steps; deploy the MT5 box first (bridge-side adapter change).

**Docs.** RUNBOOK "bridge smoke" (owner step: the `bridge-serve` token into setup step 2); TRACEABILITY row; HANDOFF and
BACKLOG 1 fragments.

**Not in scope.** The 24 h health run, measured swap, the adapter retry loop (X6), the protocol version, engine
changes, W2-4 hardening.
