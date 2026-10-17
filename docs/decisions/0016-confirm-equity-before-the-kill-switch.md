# 0016: Confirm a 12% equity read before the kill switch closes everything

Status: proposed (implementer, 2026-10-11; BACKLOG 44, trading-safety re-verification LOW-C; revised 2026-10-12 after
the trading-safety review of d2f2999: HIGH-1 sticky pending, MEDIUM-2 swap and every symbol, LOW-4 persisted; LOW-3
declined, see below; needs trading-safety re-review)

## Context
The 12% drawdown kill switch (design: Drawdown kill switch) closes every position on the account at market, moves the
stage to HALTED and returns the owner's mode to propose. Only a TOTP re-arm, then 10 days of positive shadow and 30
days propose-only, undo it. The engine's stage update in `_refresh_account` trusted a single `account().equity` read.
A terminal that reports equity 0, or a value far below the last (a reconnect, a half-filled account struct, a bridge
glitch), would therefore flatten the book at market on a number nobody else agrees with. RiskGate's entry check
already refuses an entry on equity <= 0 without running the stage machine (`invalid_fields`). The engine's own path
did not.

Two numbers the terminal reports independently describe the same equity: the balance, and the open positions'
floating P&L and swap. A real crash moves the floating P&L. A bad equity field does not.

## Decision
- **Rule.** An account read whose equity is at 12% drawdown or more from the high-water mark is *confirmed*, and the
  stage machine runs on it, when either:
  1. the balance plus the profit and swap of every position on the account (all symbols, bridge method
     `account_positions`) agrees with the equity within `risk.equity_check_tolerance` of the high-water mark (default
     2%, i.e. £20 on £1,000), the book was readable, and equity and balance are both > 0. A genuine crash is
     confirmed this way on the first read, with no delay; or
  2. an earlier 12% read is still *pending*. Two 12% reads always trip, whatever they say (two zero reads included)
     and whatever readings came in between.
- Otherwise the read starts a *pending* state:
  - `AccountState.equity_unconfirmed` is set;
  - `RiskGate.update_stage` moves no stage, not even size-down;
  - entries are refused `equity_unconfirmed`;
  - the engine logs and records the `equity_unconfirmed` decision.

  One disagreeing read never trips the kill switch.
- **Pending is sticky (review HIGH-1).** It clears only once every read has stayed below 12% minus the tolerance
  (10% by default) for `risk.equity_recovery_s` (60 s) on the engine's clock. A read in the band between 10% and 12%
  restarts that clock. Until the state clears, entries stay refused, no stage moves, and the next 12% read trips. A
  12% read, a normal read and another 12% read therefore trip, so alternating readings cannot hold the kill switch
  off. After a sustained recovery, a new 12% read needs a fresh confirmation (`equity_recovered` is recorded when the
  state clears).
- **Pending survives a restart (review LOW-4).** `stage2_pending_since` is saved in `risk_<account>.json` when the
  state starts or clears, and loaded at start. A crash loop between the two reads cannot defeat confirmation. The
  recovery clock is not saved, so a restart restarts it (the strict side).
- **Confirmation is fast.** A read that starts the pending state makes the very next tick read the account again
  (`on_tick`), instead of waiting for the normal `state_every_s` (10 s) interval or the bar close.
- **Bound.** A confirmed crash is never delayed. An unconfirmed real crash is closed on the next valid tick. In an
  active gold market that is under a second, and it is never later than the next account read the engine would have
  taken anyway. If no tick arrives, the price is not moving, and the old rule would not have read the account either.
- **Tolerance.** It leaves room for what the cross-check still cannot see: credit and rounding. Swap and positions on
  other symbols are now counted (review MEDIUM-2). Against a v4 bridge without `account_positions`, or a position
  without `swap`, the cross-check cannot corroborate, and a real crash falls to rule 2: one tick later.

## Why sticky pending and not a time window
The review offered two ways to close the alternating-readings hole:
- (a) trip on a second 12% read within a time window, whatever comes in between;
- (b) clear the pending state only after a sustained recovery below a hysteresis line.

(b) is chosen:
- **More conservative.** Under (a), sparse 12% reads more than a window apart never confirm each other, whatever
  happens between them. Under (b), a pending read is forgotten only after the account has shown a full minute of
  clearly healthy equity; reads hovering in the 10-12% band never clear it.
- **As simple.** Both keep one timestamp (the window start, or the start of the clean run) and one pending flag. (b)
  needs no window length chosen to cover the gap between reads; its only parameter is how long health must be shown.
- **It matches what it filters.** After a glitch, real reads near the true equity come back, and the state clears in
  a minute. A real crash cannot get past it: it would have to bounce back above 90% of the high-water mark for a full
  minute before its next 12% read could need a fresh confirmation.
- **The cost.** After a single glitch, entries stay refused for about a minute, and a size-down change waits as long.
  Exits are untouched.

## Why the delay is safe
- **Exits are never gated.** Nothing in this rule touches `_stop_check`, `_retry_closes`, a pending kill-switch
  flatten, the time barriers or the Friday rule. They run first on every tick, before the account is read.
- **The broker's stops are resting.** Every order goes out with its SL at the broker (OrderIntent.sl, set by RiskGate
  sizing). During the one-tick wait every position is still capped at its own stop by the broker's server,
  independent of the brain.
- **Entries are refused while pending** (fail closed), so nothing adds exposure on a number that may be wrong.
- **The trade-off favours the rule.** The risk it removes is a flatten of every position on a phantom number,
  followed by the 40-day re-arm process. The risk it adds is at most one tick of extra exposure above the stops
  already resting.

## Options considered
- *Always wait for a second read.* Simpler, but it delays every genuine crash by one read. Corroboration makes the
  common case (a real crash) instant.
- *Corroboration only.* A terminal that keeps reporting a self-consistent but bad balance and equity would never be
  questioned, and one with costs the check cannot see would never trip on rule 1. Rule 2 covers both.
- *Re-read inside the same refresh or tick (review LOW-3, declined).* The review asked for an immediate re-read when
  a bar close leaves the read unconfirmed. Declined:
  - milliseconds apart, a stuck terminal returns the same bad value twice, so rule 2 would trip on what is really one
    glitch, and the filter would vanish for every bar-close read;
  - the next tick never takes the bar-close branch again (the next close is a decision period away), so it takes the
    re-read branch (`tests/test_equity_confirmation.py::test_an_unconfirmed_read_at_a_bar_close_is_read_again_on_the_next_tick`);
  - the bound is therefore the same as for any other read.

## Protocol
- `account_positions` and `Position.swap` are bridge protocol v5 (`API_VERSION = 5`, `MIN_COMPATIBLE = 5`).
- `swap` is informational (S12): missing reads as None and counts as no swap. It never carries an outcome or a
  restriction.
- A v5 brain against a v4 bridge refuses entries (`bridge_incompatible`: a method is missing), and so does a v4 brain
  against a v5 bridge. Exits and the kill switch are unaffected.
- Deploy the MT5 box first, then the brain (RUNBOOK: Deploy order).

## Consequences
- A persistent bad equity read trips the kill switch on its second read, as two agreeing reads must. This rule
  filters single glitches, not a terminal that reports nonsense consistently.
- A rollback to a release before this ADR drops `stage2_pending_since` and trips on a single read again: the stricter
  side.
- The high-water mark itself was still raised by one balance read (`balance_closed_hwm = max(hwm, balance)`). One
  inflated balance read would raise it for good and could cause phantom 12% reads afterwards. ADR 0018 (BACKLOG 54)
  now confirms every rise.
- The supervisor's combined 12% check (`goldbot/risk/supervisor.py`) still reads the published equity. It only
  halts entries, never closes, and is re-evaluated each pass, so it was left out of scope.
- **A failed save never delays or narrows the kill switch (re-review MEDIUM-1, LOW-2).** Every risk-state save on
  the account-refresh path (the pending 12% read, the stage change, the ADR 0018 mark rise, the risk-period reset and
  the weekend rule) goes through `_save_risk_state_now`: a write that raises (a full disk) is logged and counted as a
  tick error, which blocks entries until a clean decision bar, and is never raised. The in-memory state decides: two
  uncorroborated 12% reads still trip, and the tripping read still closes every position on the account
  (`everything=True`). The next state write saves the risk state again; until one succeeds, a restart would reload
  the last saved state (the halt, the pending read or the mark may be lost), and the health check sees the tick
  errors.
- **Correction (trading-safety F1, 16 Oct).** The bullet above was false in auto mode: the trip's write of the owner's
  mode back to propose (`ApprovalBus.set_mode`) ran before the flatten, so a full disk raised out of it and nothing
  was closed on the tripping read; a positions() read failing on that read did the same. Later runs were narrowed to
  this engine's magic range, so a manual or other-magic position stayed open. It now holds in auto mode too: the
  close-everything choice is state (`_kill_everything`), set before any I/O, saved with the risk state
  (`kill_everything`), used by every retry and after a restart, and cleared only once a run has closed every XAUUSD
  position on the account (all magics). "The whole account" means exactly that: `MT5Broker.positions()` reads the
  engine's symbol only, so positions in other symbols are not closed; whether to flatten them is an owner decision.
  The mode write comes after the flatten and never raises (a tick error, retried; the in-memory
  `approval_mode = "propose"` already blocks auto entries); a halted engine retries it after a restart too
  (`test_a_failed_mode_write_on_a_flat_trip_is_retried_after_a_restart`). Evidence: `tests/test_kill_switch_everything.py`
  (scenario A `test_a_failed_approval_mode_write_on_the_trip_still_closes_the_other_magic_position`, scenario B
  `test_a_failed_positions_read_on_the_trip_still_closes_the_other_magic_position_on_the_next_refresh` and
  `..._on_the_next_tick`, restart `test_after_a_restart_a_saved_close_everything_still_closes_the_whole_account`).
  A rollback to an earlier release ignores the saved flag: the narrower, pre-fix behaviour.
- **What would change this.** A measured bad read lasting two ticks would call for confirmation by time (reads N
  seconds apart). A bridge that reports credit would let the tolerance shrink.

## Amendment (BACKLOG 62, 2026-10-17): the kill switch closes every symbol

Decided 2026-10-16 by the program director and product owner (owner delegated: it tightens risk and loosens
nothing); plan `docs/proposals/2026-10-backlog62-kill-switch-all-symbols-plan.md` with the trading-safety conditions
C1-C9. It replaces the line of the F1 correction above that "positions in other symbols are not closed; whether to
flatten them is an owner decision".

- **What.** "Close everything" (DESIGN.md:122, "12%: close everything at market immediately") means every position on
  the account: every symbol and every magic. While `_kill_everything` is set, the kill switch and the close retries
  read `account_positions()` and close what it lists, each with that symbol's own quote and fill mode
  (`MT5Broker.close`, `_filling(symbol)`).
- **Why.** The 12% is measured on the account's equity, which already counts every symbol's P&L (the cross-check
  above reads `account_positions()`). A losing position on another symbol kept draining the account after the halt;
  closing everything matches what the switch measures.
- **The owner's manual trades are closed.** A trade opened by hand on any symbol, or another engine's, is closed at
  market when the switch trips. There is no confirmation step for it: exits are never gated. RUNBOOK (Halt and
  re-arm) and OWNER_GUIDE say so in plain words; a one-time owner notice goes on issue #34 before the deploy (C8).
- **Unreadable account book.** If `account_positions()` fails, the switch closes what `positions()` returns (the
  engine's symbol, every magic: never fewer closes than before) and the flag stays set (C1). The flag clears only on
  a successful `account_positions()` read that lists nothing; every close returning ok is not enough (C2). A due close
  of another symbol is never dropped on the fallback read (C3). The failure is logged and journalled
  (`book_unreadable`, `read: account_positions`) once per streak, and the owner-mode reset still runs (C6).
- **A symbol without a quote.** MT5 selects the symbol before reading its tick; no symbol info or no tick is a
  refused close (10021). On another symbol (hidden or delisted: refused on every attempt) it is backed off with the
  market-closed table (10017/10018: reconcile_every_s, doubling up to modify_backoff_max_s), so the journal gets one
  row per backoff step and the adapter logs it once per streak (trading-safety LOW 1); on the engine's own symbol a
  10021 stays a transient refusal retried every CLOSE_RETRY_MIN_S. The flag stays set meanwhile. One symbol's refusal
  never stops the others. Every due close records its symbol, so the fallback drop rule never assumes one (LOW 4).
- **Out of scope.** Pending orders on other symbols are not cancelled (goldbot places none; a pending order the owner
  left could fill after the flatten, and the next run while the flag is set closes that position). A partial close
  of another symbol is never sent (its volume step is not read). Multi-account flattening and bridge protocol v6 are
  not part of this change. SRE follow-up (C9): a health WARN when `kill_everything` stays set with closes due.
- **Evidence.** `tests/test_kill_switch_everything.py` (BACKLOG 62 section), `tests/test_close_any_symbol.py`.
