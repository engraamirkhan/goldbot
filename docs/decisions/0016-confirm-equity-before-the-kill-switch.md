# 0016: Confirm a 12% equity read before the kill switch closes everything

Status: proposed (implementer, 2026-10-11; BACKLOG 44, trading-safety re-verification LOW-C; needs trading-safety
review)

## Context
The 12% drawdown kill switch (design: Drawdown kill switch) closes every position on the account at market, moves the
stage to HALTED and returns the owner's mode to propose. Only a TOTP re-arm, then 10 days of positive shadow and 30
days propose-only, undo it. The engine's stage update in `_refresh_account` trusted a single `account().equity` read.
A terminal that reports equity 0, or a value far below the last (a reconnect, a half-filled account struct, a bridge
glitch), would therefore flatten the book at market on a number nobody else agrees with. RiskGate's entry check
already refuses an entry on equity <= 0 without running the stage machine (`invalid_fields`). The engine's own path
did not.

Two numbers the terminal reports independently describe the same equity: the balance, and the open positions'
floating P&L. A real crash moves the floating P&L. A bad equity field does not.

## Decision
- **Rule.** An account read whose equity is at 12% drawdown or more from the high-water mark is *confirmed*, and the
  stage machine runs on it, when either:
  1. the balance plus this symbol's open P&L agrees with the equity within `risk.equity_check_tolerance` of the
     high-water mark (default 2%, i.e. £20 on £1,000), the book was readable, and equity and balance are both > 0. A
     genuine crash is confirmed this way on the first read, with no delay; or
  2. the previous account read was also at 12% or more. Two consecutive 12% reads always trip, whatever they say,
     two zero reads included.
- Otherwise the read is *unconfirmed*: `AccountState.equity_unconfirmed` is set, `RiskGate.update_stage` moves no
  stage (not even size-down), entries are refused `equity_unconfirmed`, and the engine logs and records the
  `equity_unconfirmed` decision. One disagreeing read never trips the kill switch. A read below 12% clears the
  pending state, so a 12% read, a normal read and another 12% read do not trip.
- **Confirmation is fast.** An unconfirmed read makes the very next tick read the account again
  (`on_tick`), instead of waiting for the normal `state_every_s` (10 s) interval or the bar close.
- **Bound.** A confirmed crash is never delayed. An unconfirmed real crash is closed on the next valid tick: in an
  active gold market that is under a second, and always no later than the next account read the engine would have
  taken anyway. If no tick arrives, the price is not moving, and the old rule would not have read the account either.
  The pending state is in memory. A restart between the two reads costs one more read. It never trips on a single
  read.
- The tolerance gives room for what the open P&L leaves out: swap (MT5 `position.profit` excludes it), positions on
  another symbol, and credit. When that difference is above 2%, a real crash falls to rule 2: one tick later.

## Why the delay is safe
- **Exits are never gated.** Nothing in this rule touches `_stop_check`, `_retry_closes`, a pending kill-switch
  flatten, the time barriers or the Friday rule. They run first on every tick, before the account is read.
- **The broker's stops are resting.** Every order goes out with its SL at the broker (OrderIntent.sl, set by RiskGate
  sizing), so during the one-tick wait every position is still capped at its own stop by the broker's server,
  independent of the brain.
- **Entries are refused while unconfirmed** (fail closed), so nothing adds exposure on a number that may be wrong.
- The risk the rule removes, a market-wide flatten on a phantom number followed by a 40-day re-arm process, is
  larger than the risk it adds, which is at most one tick of extra exposure above the stops already resting.

## Options considered
- *Always wait for a second read.* Simpler, but it delays every genuine crash by one read. Corroboration makes the
  common case (a real crash) instant.
- *Corroboration only.* A terminal that keeps reporting a self-consistent but bad balance and equity would never be
  questioned, and one with swap above the tolerance would never trip on rule 1. Rule 2 covers both.
- *Re-read inside the same refresh.* Milliseconds apart, a stuck terminal returns the same bad value twice. The next
  tick is a better independent read and still well inside the bound.

## Consequences
- A persistent bad equity read trips the kill switch on its second read, as two agreeing reads must. This rule
  filters single glitches, not a terminal that reports nonsense consistently.
- The supervisor's combined 12% check (`goldbot/risk/supervisor.py`) still reads the published equity. It only
  halts entries, never closes, and is re-evaluated each pass, so it was left out of scope.
- What would change this: a measured case of a bad read lasting two ticks (then confirmation by time, e.g. reads
  N seconds apart), or a bridge that reports swap per position (then the tolerance can shrink).
