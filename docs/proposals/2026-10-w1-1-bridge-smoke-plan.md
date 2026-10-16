# Plan: W1-1 bridge smoke (BACKLOG 1)

Status: plan rev 2, architect (A-PLAN), 2026-10-16. Trading-safety review: ADR 0021 APPROVE-WITH-CONDITIONS (fences
F1-F6, all in the ADR); plan CHANGES REQUESTED (S1-S11, answered below). Build Fri 10-23; demo run Mon 10-26.

**Requirement.** BACKLOG 1: `run.py bridge-smoke` prints login, server, balance and a live XAUUSD tick, then opens
and closes 0.01 lot on a demo account; non-zero exit if not demo. Design: RiskGate only path; owner confirms every
entry (90 s); exits never gated.

## Decisions (new TRACEABILITY row)
1. Entry passes `RiskGate.check_operational` (ADR 0021) and **one owner click on ApprovalBus** (F6); no click in 90 s
   is EXPIRED_UNAPPROVED, exit 3, nothing sent. Approval re-runs the check (A3).
2. Login printed masked (`****123`): output goes on public issue #34.
3. Magic `magic_base+99`; a CI test proves no family hashes to 99 (S7; `runner.py:2995`).
4. **X7 departure (S9):** no `pending_orders` row (the engine's table). Instead: deterministic comment
   `cid[-31:]` (cid `{account}-{epoch_ns}-smoke`, as `runner.py:1315`), and a 24 h look-up of positions and deals
   with magic +99 before sending.

## Files
| File | Change |
|---|---|
| new `ops/smoke.py` | `smoke_state(...) -> AccountState`, `run_smoke(broker, bus, acc, state_dir, *, now) -> SmokeReport`, `main(argv) -> int` |
| `ops/commands/ops.py:28`, `ops/run.py:13` | register `bridge-smoke` (pos 210), usage line |
| `risk/gate.py:391-544` | pure split into shared helpers (F5); `check_operational` (F1, F2, S6) |
| `execution/bridge.py:263` | `GuardedBroker.place_order` refuses +99 unless terminal reports demo (F4; protocol, digest unchanged) |
| `ops/ledger.py:303` | smoke positions listed, not RC-8 mismatches (coordinate W1-4) |

Must not change: `METHODS`/`API_VERSION`, `runner.py`, `broker.py` records, `ApprovalBus` API.

## Data flow (brain)
1. `bridge_endpoint` → `RemoteBroker` → `check_bridge`; `verify_terminal_account` and `acc.mode == "demo"`, else exit 2.
2. Print reads. Preconditions, else exit 4 (S3): `systemctl is-active goldbot-engine@<acc>` is `inactive` **and**
   loop heartbeat (`watchdog.py:44`) >= 150 s old; `flock` (`LOCK_NB`, file mode 0600) on `state/.smoke-<acc>.lock`,
   taken before any order call and held to the end; cannot create or take it → exit 4, nothing sent. The supervisor
   service stays running (`engine_should_halt` refuses supervisor state older than 60 s).
3. Look-up (S8, S9): a leftover smoke position is closed first; if it cannot be closed, exit 5, never open another.
4. **`smoke_state` (S2), the only builder, every field explicit:** equity/balance/margin from `account()`;
   hwm, day/week start, stage, `equity_unconfirmed` from `risk_<acc>.json`, stale baseline passed through
   `new_day()` in memory (S5, `gate.py:550`); positions, open risk, long/short from `account_positions()`; spread,
   tick age, tick-ahead from `last_tick`; `stale_bars` from `get_bars`; owner halt from `bus.control()`; supervisor
   halt and other-account exposure from `engine_should_halt`/`read_state` (`supervisor.py:224,233`); windows,
   rollover, weekend, market closed from `risk/windows.py` and `trading_sessions`; `account_class` from the classifier
   file (missing → `unknown`, refused); drift, clock, bridge flags from their stores; `combined_size_down` from
   `read_state()["size_down"]` (missing key refuses; engine: `runner.py:1929`); `in_blackout` (tier-1 news) from the
   engine's calendar (unreadable refuses; `runner.py:1932`); `dq_error` = stale bars or a failed tick/bars read,
   `tick_error` = `last_tick` raised (refuses), `data_recovering` = False, listed as a constant with its reason
   (engine-only state, `runner.py:1904-1920`). An unreadable source refuses. Every field maps to a named source or a
   listed constant with a reason; a bare default fails `test_smoke_state_sets_every_account_state_field`.
5. `check_operational` → Proposal on the bus → wait 90 s → re-check S3 and the gate → `place_order` (long only, S11).
6. Verify the fill's sl/tp (S8); close. Close retried 3x, never gated; still open → exit 5 LEFT OPEN plus Telegram alert.
7. `BridgeUnavailable`/`BridgeError` on send (S4): poll positions and deals >= `order_timeout_s + ORDER_MAX_AGE_S`
   (25 s); found → close; not found → exit 6 UNKNOWN; never re-send.

**Order shape (S11):** buy; `stop_distance` from `sizing.stop_distance`, at or beyond stops level + spread;
sl = ask − d, tp = ask + d; lots = `volume_min` rounded down to `volume_step`.

## Failing tests first
`tests/test_smoke.py`: `test_prints_masked_login_server_balance_and_tick_then_opens_and_closes_the_minimum_lot`,
`test_full_login_never_appears_in_the_output`, `test_exits_2_when_the_terminal_is_not_demo`,
`test_no_click_in_90_s_expires_exits_3_and_sends_nothing`, `test_a_rejection_sends_nothing`,
`test_approval_re_runs_the_gate_and_a_halt_meanwhile_sends_nothing`, `test_engine_active_or_heartbeat_149_s_refuses_150_s_inactive_allows`,
`test_engine_started_during_the_wait_is_caught_before_place_order`, `test_a_second_smoke_cannot_take_the_lock`,
`test_smoke_state_sets_every_account_state_field` (model_fields coverage), `test_each_source_mutated_to_unsafe_refuses`
(mutation), `test_a_stale_baseline_is_reset_in_memory_and_never_written`, `test_bridge_error_on_send_polls_25_s_then_exits_6_and_never_resends`,
`test_a_found_fill_after_a_timeout_is_closed`, `test_missing_sl_or_tp_after_the_fill_closes_at_once`,
`test_left_open_exits_5_and_alerts`, `test_an_unclosable_leftover_blocks_a_new_order`,
`test_a_smoke_deal_in_the_last_24_h_is_found_by_its_comment`, `test_stop_and_target_sides_and_stops_level`,
`test_lots_are_volume_min_on_the_step`, `test_close_path_never_calls_the_gate`.

`tests/test_risk_gate_split.py`: `test_check_decisions_unchanged_on_a_state_grid` (F5),
`test_operational_refuses_operational_not_demo_for_live_or_an_unreadable_phase` (F1; a missing phase file is phase 0
and allowed on demo, a corrupt one raises and refuses),
`test_operational_has_no_lots_p_target_or_cost_parameter` (F2, S6), `test_operational_keeps_account_class_unknown`,
`test_operational_daily_cap_refuses_at_equality`, `test_operational_absolute_projection_allows_equality` (S10).
`tests/test_operational_fence.py`: `test_only_ops_smoke_calls_check_operational`,
`test_engine_allocator_api_telegram_never_import_ops_smoke` (F3, AST).
`tests/test_bridge.py::test_guarded_broker_refuses_smoke_magic_unless_demo` (F4);
`tests/test_magic.py::test_no_family_magic_equals_the_smoke_offset` (S7);
`tests/test_ledger.py::test_smoke_positions_are_listed_not_rc8_mismatches`.

## Risks
| Risk | Test |
|---|---|
| Split changes engine decisions | decision grid |
| Smoke and engine both trading | service + heartbeat + lock + re-check |
| Duplicate order | no order retry (`bridge.py:352`); exit 6 test |
| Position left unprotected | sl/tp verify; LEFT OPEN alert |
| Login on #34 | masked-output test |

**Reviews.** Trading-safety (gate, ADR 0021, order/close), security (masking, lock file mode), SRE (RUNBOOK: stop
engine, keep the supervisor running, smoke, start; MT5 box first for F4).
**Review record.** trading-safety-reviewer 2026-10-16 @ d1d86d6: ADR 0021 APPROVE (F1-F6); plan APPROVE with the
G1a-c text fix (`combined_size_down`, `in_blackout`, dq/tick flags), now applied; checked at code review through the
coverage and mutation tests.
**Docs.** RUNBOOK "bridge smoke"; TRACEABILITY row with the X7 departure; HANDOFF, BACKLOG 1 fragments.
**Not in scope.** 24 h health run, measured swap, X6 retry loop, protocol bump, engine changes, W2-4.
