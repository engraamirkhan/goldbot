# 0074: One engine per account, one SymbolBook per instrument

Status: proposed (implementer, lane L3, 2026-11-02; trading-safety review required before merge)

## Context
The owner directed all 26 listed instruments onto the demo accounts in one release (multi-instrument plan 2026-11-02,
Option B: "one engine per account with N SymbolBooks; one AccountRisk (RiskGate, AccountState, kill switch) shared;
serial gate.check across symbols"). The engine (`goldbot/engine/runner.py`, ~3,000 lines) was written for one
symbol: about sixty attributes hold one instrument's state, interleaved with the account's risk state. The plan's
non-negotiables are one AccountState per account, a serial gate so the per-account loss limits cover every symbol,
exits never gated, and XAUUSD behaviour byte-identical.

Options considered:
1. N independent `Engine` objects sharing one RiskGate and AccountState. Every engine would roll risk periods, run the
   stage machine, write the risk and engine files and run the kill switch: a primary/secondary split threaded through
   every account-level method, and two writers of one risk file.
2. A new `SymbolBook` class holding the per-instrument state, with every runner method rewritten to take a book. A
   rewrite of most of runner.py, against the ADR 0023 move-only ratchet and three lanes editing the same file.
3. (chosen) The Engine stays the account's engine. The per-instrument attributes (`books.BOOK_ATTRS`) become data
   descriptors that read and write the ACTIVE book; `books.using(engine, book)` switches books. Every method runs on
   one instrument at a time with the code that runs gold today.

## Decision
Option 3 (`goldbot/engine/books.py`). Gold is the primary book, built from EngineConfig exactly as before; an engine
with no other instrument never switches, so gold's paths are unchanged (pinned by a four-day replay against main).
The run loop takes the books in turn on one thread, so gate.check is serial. The AccountState is one object: the
account's figures (equity, stage, day start, exposure, halts, bookings) are shared; the facts the gate reads of the
instrument being decided on (`books.SYMBOL_STATE`: spread, quote age, staleness, data quality, market hours, blackout,
Friday cut, entry windows) are swapped per book on each switch, so no check runs on another symbol's spread or on a
copy that dropped a booking. An entry is booked (`RiskGate.book`) right after its order went out. Each non-gold book
keeps its own order rows and open trades file; the risk file and engine_<account>.json stay the account's. Non-gold
books are demo only, start with ATR-scaled random controls only, and refuse entries on any unknown term
(instruments.db unreadable, no swap conversion, no spread cap, unconfirmed registry terms).

## Consequences
- Small runner.py diff; the ratchet still falls (closed-trade record moved out).
- A new per-instrument attribute must be added to BOOK_ATTRS, or it is silently account-wide. A test holds every
  BOOK_ATTRS name set on a new book and none on the Engine instance; reviewers check new `self.` attributes in
  `_init_book` vs `__init__`.
- Each book refreshes the account on its own cadence (one account and positions read per book per `state_every_s`),
  more broker reads than one engine; acceptable on demo, to be shared with a short-lived cache if the bridge struggles.
- Measured: 26 books decide one 15m bar close in 3.9 s on the dev Mac (budget 20 s, `tests/test_capacity.py`).
- What would change this decision: a live (real-money) multi-instrument account, which would need a review of every
  account-level method for per-symbol assumptions; or the books' bridge reads exceeding the box's capacity.
