# Plan: BACKLOG 62 — the 12% kill switch closes every position on the account (all symbols)

Status: plan, architect, 2026-10-17; trading-safety plan review before any build (throughput plan, lever 2).

**Requirement.** DESIGN.md:122 "12%: close everything at market immediately" (TRACEABILITY R13, R39 / ADR 0016).
Replaces ADR 0016:127 ("other symbols are not closed; an owner decision") — decided by PD+PO 2026-10-16 as a
tightening.

**Today.** `_kill_switch` (`runner.py:2206`) reads `_read_positions` (`:1476`) → `broker.positions()`, gold-only on MT5
(`mt5_adapter.py:379`). `close` (`mt5_adapter.py:363`) uses `p.symbol` and its tick but the fill mode from
`_filling()` (`:320`), which reads `self.symbol`. **Bug the backlog missed:** `_retry_closes` (`runner.py:1678`) also
reads `positions()` and drops any due close whose position it cannot see (`:1693`), so a refused EURUSD close would be
forgotten and never retried.

## Changes (two PRs, disjoint files)

**PR-A, broker layer.** `mt5_adapter.py:320` → `_filling(self, symbol)`; `place_order` passes `intent.symbol`, `close`
passes `p.symbol`. For a non-engine symbol, `close` first `symbol_select(p.symbol, True)`; no tick or symbol info →
refused `OrderResult`, never a raise. A full close sends `p.volume` (no rounding); a partial close of another symbol
is never sent. `paper.py:44` keeps the last tick per symbol; an unseen symbol raises, so the close is refused and
retried. `bridge.py`: no change, no protocol bump (`GuardedBroker.close` passes through; `RemoteBroker.close` and v5
`account_positions` exist); the notional ceiling (`place_order`, `_notional_refusal`) is untouched.

**PR-B, engine** (after the runner.py lane releases it). New `_read_kill_book() -> (positions, whole) | None`: while
`_kill_everything` is set it reads `account_positions()`; on failure it falls back to `_read_positions` with
`whole=False`. `_kill_switch` uses it and clears the flag (`:2239`) only when a whole-account book is flat; a fallback
run never clears it. `_retry_closes` uses the same book while the flag is set; a due close is dropped only when it is
missing from a whole-account book. `state.open_positions` (`:2236`) counts only this engine's positions.

**Flow.** Trip sets `_kill_everything` (saved) → `account_positions()` → `_try_close(pid)` per position (never gated) →
`broker.close(pid)` with that symbol's tick and fill mode → a refused close goes to `_close_due` with the market-closed
backoff → `_retry_closes` resends against the whole-account book → the flag clears when the whole account reads flat.

## Failing tests first

`tests/test_kill_switch_everything.py`: `test_the_trip_closes_a_eurusd_and_an_xauusd_position`,
`test_a_refused_eurusd_close_is_retried_and_does_not_block_the_xauusd_close`,
`test_a_due_eurusd_close_is_not_dropped_by_a_gold_only_retry_read`,
`test_the_flag_stays_set_until_account_positions_reads_flat`,
`test_an_unreadable_account_book_closes_what_positions_returns_and_stays_pending`,
`test_after_a_restart_a_saved_flag_still_closes_the_eurusd_position`,
`test_after_the_account_is_flat_halted_refreshes_close_only_this_engines_magic`.
Adapter/bridge: `test_close_reads_the_fill_mode_of_the_positions_own_symbol`,
`test_close_with_no_tick_for_another_symbol_returns_refused_not_raises`,
`test_paper_and_bridge_close_a_non_engine_symbol`, `test_the_notional_ceiling_ignores_closes`.

**Docs.** ADR 0016 amendment (all symbols, why, manual trades are closed); RUNBOOK owner warning near :856; TRACEABILITY
R13/R39 fragments (reading: "close everything" = every symbol and magic; a fallback read never clears the flag);
HANDOFF fragment.

**Non-negotiables.** No new order path; exits never gated (`_try_close` unchanged); no secrets (fake tickets).
**Risks.** The owner's manual trades on other symbols are closed (RUNBOOK warning; ICM demo only for now); a symbol with
no live quote is retried with backoff and the flag stays set; a second engine on the account is also closed (intended).
**Reviews.** Trading-safety on this plan; after the build: code-reviewer, trading-safety APPROVE (E1), security before
push. **Out of scope:** per-symbol volume step for partial closes, multi-account flattening, protocol v6.

## Conditions from the trading-safety plan review (APPROVE-WITH-CONDITIONS, 9301fb9, 2026-10-17)

Binding on the build; they override the text above where they differ.
- **C1** Two separate values: *what to close* is every position in the book, any magic, whenever `_kill_everything` is
  set (the fallback book included); *may the flag clear* is true only for a book read by `account_positions()`. A
  fallback run never closes fewer positions than today. Test: `account_positions` raises and an OTHER_MAGIC XAUUSD
  position is still closed while the flag stays set.
- **C2** The flag clears only on a successful `account_positions()` read that returns zero positions — not when every
  close in a run returned ok. An all-ok run keeps `_kill_pending` for one more read.
- **C3** Paper `positions()` filters to `self.symbol` and `account_positions()` returns every symbol (as MT5 does), so
  the gold-only retry test fails on the base commit; show that failure.
- **C4** Paper gets per-symbol quotes through an explicit input (e.g. `set_quote(symbol, tick)`; `on_tick` keys to
  `self.symbol`); `_check_exits` prices each position with its own symbol's tick and skips a symbol with none;
  `_close_at` reads the tick before any state change.
- Build-time: **C5** `symbol_info`/tick None → refused `OrderResult` retcode 10021 (never a raise), `symbol_select`
  before every tick read; **C6** `_read_kill_book` inside the existing try so `_reset_owner_mode` always runs, an
  `account_positions` failure logged once per streak; **C7** `open_positions` counts `cfg.symbol` positions of any magic
  (same as `_refresh_account`); **C8** `kill_switch_close` rows carry `symbol`, `magic`, `lots`; RUNBOOK + OWNER_GUIDE
  warning; one-time owner notice on #34 before deploy; no confirmation gate on closing manual trades (exits are never
  gated). **C9** (ADR/SRE): pending orders on other symbols are not cancelled (out of scope, stated in the ADR 0016
  amendment); SRE follow-up health WARN when `kill_everything` stays set with closes due.
- Extra tests: 10010 partial close resent with remaining volume; 10018 back-off while XAUUSD still closes; due EURUSD
  close retried through `no_quote_pass`; auto-mode trip with `account_positions` raising still resets to propose; paper
  EURUSD without a quote never breaks XAUUSD stop checks; adapter `symbol_info` None and tick None refused with 10021;
  every `kill_switch_close` row carries symbol and magic. `test_the_notional_ceiling_ignores_closes` uses a non-gold,
  foreign-magic position.
