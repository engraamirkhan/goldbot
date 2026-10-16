# Plan: BACKLOG 66 — equity-scaled notional ceiling in GuardedBroker

Status: plan, architect, 2026-10-16. Trading-safety plan review before any build (throughput plan, lever 2).

**Source.** DESIGN.md:112: combined exposure capped at 3 lots or 30% of combined equity notional; 30% × 1:20 = 6×
equity, which `gate.py:540-543` enforces. TRACEABILITY R7 covers the gate only; a new row (`new-row R --slug
bridge-notional-ceiling`) cross-references R7. Why now: the demo terminal reports 1:5000 leverage (£0.63 margin per
0.01 lot; ~15.9 lots possible on £1,000), and the bridge's only independent ceiling is a fixed 3.0 lots.

**Today.** `GuardedBroker.place_order` (`bridge.py:263-271`) checks the magic, `0 < lots <= MAX_ORDER_LOTS`
(`:104`, `:266`) and the verified account. `close` (`:277`) and `modify` (`:273`) never size.

## Change (`goldbot/execution/bridge.py`, `goldbot/engine/runner.py:1388`)

- New `GuardedBroker._notional_refusal(intent, info: AccountInfo) -> str | None`, run after the 3.0-lot and account
  checks (reusing that `account()` read): magic → 3.0 lots → account → F4 fence → ceiling.
- **All reads on the MT5 box** (only `lots`, `side`, `symbol` come from the brain): `info.equity` and `currency`;
  `symbol_info(sym).contract_size`; `last_tick` (ask for a buy, bid for a sell, the price
  `MT5Broker.place_order` uses, `mt5_adapter.py:328`); `fx = sizing.account_fx(currency, loss_per_lot(sym, side,
  px, 1.0) / contract)`; `account_positions()` filtered to `sym`.
- **Formula:** refuse when `(Σ open lots + intent.lots) × contract × px × fx > 6 × equity` (strict `>`, as the gate).
- **Fail closed:** any read that raises, is missing, non-finite or ≤ 0, or `fx` None → refuse `unreadable:<field>`.
- **Refusal form:** `OrderResult(ok=False, retcode=10006, order_id=None, filled_lots=0,
  message="bridge_notional_ceiling: ...")`, not a raise. 10006 is in `DEFINITE_REJECT_RETCODES` (`runner.py:96`), so
  the engine records `rejected`, never "maybe sent". `runner.py:1388` adds `"message": res.message` to the order
  journal entry (additive).
- **Protocol unchanged:** internal to the bridge like F4; `METHODS`, signatures, `OrderResult` fields,
  `API_VERSION`/`MIN_COMPATIBLE` (5) and `protocol_digest` unchanged (`test_bridge_compat` pins it).
- **Must NOT change:** `close`, `modify`, `MAX_ORDER_LOTS`, `gate.py`, `RemoteBroker`, `mt5_adapter.py`.

## Readings (recorded on the TRACEABILITY row)

1. **Total gross exposure:** all positions on the symbol, any magic, both sides; a per-order cap could be beaten by
   several orders, and the gate's combined figures are gross too.
2. **This terminal's equity only:** the bridge cannot see the other account and must not trust the brain; with two
   accounts it can be stricter than the gate; with one it matches.
3. **No minimum-lot exception:** 0.01 lot at £1,000 is ~3.1× and fits; below ~£525 even 0.01 is refused, by intent.
4. **W1-1 / ADR 0021:** the 0.01-lot smoke passes (3.1×); F4 and F1-F6 untouched; the ceiling runs after F4.

## Failing tests first (`tests/test_bridge.py` unless noted)

`test_at_1000_equity_one_hundredth_lot_passes_and_two_hundredths_is_refused`,
`test_notional_exactly_six_times_equity_passes_and_one_cent_over_is_refused`, `test_ceiling_scales_with_equity_at_10000`,
`test_open_position_notional_counts_toward_the_ceiling`, `test_gbp_account_converts_with_the_terminal_figure`,
`test_unreadable_equity_price_contract_fx_or_positions_refuses` (parametrised: NaN, 0, raising),
`test_close_of_a_three_lot_position_goes_through`, `test_modify_is_never_sized`,
`test_three_lot_outer_bound_still_refuses_above_three`, `test_refusal_is_a_definite_reject_and_crosses_the_wire_with_its_reason`,
`tests/test_engine_safety.py::test_a_bridge_ceiling_refusal_reaches_the_journal_with_its_message`;
`tests/test_bridge_compat.py` pins stay green. No state, so no restart test (re-reads the terminal every order).

## Risks

1. **File overlap:** W1-1's F4 edits the same function — start this lane after the F4 commit merges.
2. **Extra terminal reads** lengthen the order call (counts against `ORDER_MAX_AGE_S`); measure it in the smoke.
3. **Retcode 10006 is a real MT5 code;** the message prefix distinguishes our refusal.
4. **Bridge deployed before the brain:** the reason is logged only on the box until the `runner.py` change deploys.

**Deploy:** MT5 box first (the guard works with any brain), then the brain; one RUNBOOK line.
**Reviews:** trading-safety on this plan, then on the build; code-reviewer; security PUSH-OK; sre for the deploy.
**Docs:** HANDOFF fragment, the new TRACEABILITY row, the bridge module docstring (`bridge.py:19-22`).
**Out of scope:** lowering 3.0, gate changes, cross-account totals on the bridge, exits, and the existing magic/lots
guards still raising `PermissionError` (follow-up item). **Owner decisions:** none.
