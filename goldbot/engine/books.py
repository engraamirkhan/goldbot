"""One engine per account, one SymbolBook per instrument (multi-instrument demo plan 2026-11-02, Option B, lane L3).

The plan's rules, as built here:

* "one engine per account with N SymbolBooks; one AccountRisk (RiskGate, AccountState, kill switch) shared; serial
  gate.check across symbols". The `Engine` stays the account's engine: its RiskGate (`gate`), its AccountState
  (`state`), the kill switch and risk-period state, the approvals, the order sender and every account file are the
  account's, once. What belongs to one instrument (its bars, resamplers, ticks, cost and contract caches, open trades
  and order rows, exploration arms, session table, magic range, journal) lives on a `SymbolBook`. Engine attributes
  named in BOOK_ATTRS are data descriptors that read and write the ACTIVE book, so every method runs on one
  instrument at a time with the code that runs gold today. The run loop takes the books in turn (`account_pass`), one
  thread, so gate.check is serial across symbols and every check sees the exposure the previous one left.
* "XAUUSD paths byte-identical": gold is the primary book (`Engine.primary`), built from the EngineConfig as before.
  An engine with no other instrument never switches books, so nothing here runs for it.
* The AccountState holds the account's facts once (equity, stage, day start, open exposure, halts) plus the facts the
  gate reads of the instrument being decided on (SYMBOL_STATE: spread, quote age, staleness, data quality, market
  hours, the entry windows). A switch (`using`) keeps each book's own copy of SYMBOL_STATE, so an entry on one symbol
  is never gated on another symbol's spread or market hours; a book that has not refreshed yet reads stale and
  closed (fail closed).
* "SymbolBook has no order method": a book is state only. Orders still go through `Engine._execute` after
  `RiskGate.check`, and exits through the engine's close path, on the active book.
* Exposure is account-wide (plan: "account-wide heat runner.py:1057, :2115"): with more than one book every refresh
  counts every position on every book's symbol (`after_refresh`), priced on its own book's quote, contract and the
  broker's conversion; one it cannot price makes the open risk unknown, which the RiskGate refuses (fail closed).
* Each non-gold book keeps its order rows and open trades in state/books/<account>/orders_<store_symbol>.json (never
  `orders_*.json` in state/, which the API reads as accounts); the risk file and engine_<account>.json stay the
  primary's (`BookStore`). Its lot and swap terms come from state/instruments.db (`refresh_terms`); unreadable or
  missing terms refuse that book's entries while its exits run.
* Live and observe-only accounts never get a non-gold book (`open_books` refuses; the accounts loader refuses first).
"""
from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, Any, Generic, ParamSpec, TypeVar

import pandas as pd

from goldbot.base import FrozenRecord
from goldbot.engine import magic
from goldbot.engine.exploration import ExplorationConfig
from goldbot.engine.instruments_db import InstrumentsDb, InstrumentsDbError, InstrumentTerms, terms_from_info
from goldbot.engine.store import JsonEngineStore, OrdersSnapshot, RiskSnapshot
from goldbot.instruments import GOLD, InstrumentSpec

if TYPE_CHECKING:
    from goldbot.engine.runner import Engine, EngineConfig
    from goldbot.execution.broker import SymbolInfo, Tick
    from goldbot.labels.triple_barrier import SwapSpec
    from goldbot.risk import Intent
    from goldbot.risk.gate import GateDecision

log = logging.getLogger("goldbot.engine")

# Engine attributes that belong to one instrument (read and written on the active book). Everything else on the
# Engine is the account's. A test holds every name here set on a new book and none left on the Engine itself.
BOOK_ATTRS: tuple[str, ...] = (
    "cfg", "sessions", "agents", "models", "live_shares", "live_versions", "shadow", "shadow_models",
    "probe", "exploration", "state_store",
    "ticks", "bars_1m", "history", "broker_m1", "last_bar_close", "_closed_through", "_history_through",
    "_ctx_cache", "_resamplers", "_bars_clean", "_last_tick", "_skew", "_unhealthy_at",
    "_dq_pending", "_dq_bar_events", "_dq_repeats", "_late_ticks", "_late_since_warn", "_late_warned_at", "_late_bar",
    "_late_streak", "_quote_invalid_since", "_invalid_state_written",
    "_tick_log", "_tick_flush_retry", "ticks_dropped", "decisions", "_journaled", "_exit_fills", "_excursion_errors",
    "_cost_cache", "_sym_cache", "_fx_cache", "_contract", "_contract_tried", "_min_lot_risk", "_open_fx",
    "_price", "_ladder", "_lot_terms_warned", "_last_atr", "_last_terms", "_blackout_event",
    "open", "sent_ids", "_orders", "_closes", "_records_lost", "_looks", "_unconfirmed", "_position_ids",
    "_close_due", "_close_backoff", "_modify_backoff", "_foreign", "_kill_pending", "_kill_book_failing",
    "_weekend_done", "_read_fails", "_read_fail_at", "_last_reconcile", "_last_state_write",
)

# AccountState fields that describe the instrument being decided on; every other field is the account's.
# `blackout_kind` goes with `in_blackout` (trading-safety review of #310, L1): one book's news shock never reads as
# another book's tier-1 blackout, nor the reverse
SYMBOL_STATE: tuple[str, ...] = (
    "last_tick_age_s", "spread_points", "tick_ahead_s", "in_blackout", "blackout_kind", "dq_error", "stale_bars",
    "data_recovering", "weekend", "market_closed", "clock_mismatch", "tier2_blackout", "reopen_window",
    "holiday_thin", "calendar_unavailable",
)
# a book that has not refreshed yet: stale and closed, so the gate refuses its entries until its first refresh
FRESH_SYMBOL_STATE: dict[str, Any] = {"last_tick_age_s": math.inf, "spread_points": math.inf, "tick_ahead_s": 0.0,
                                      "in_blackout": False, "blackout_kind": None, "dq_error": True,
                                      "stale_bars": True,
                                      "data_recovering": False, "weekend": False, "market_closed": True,
                                      "clock_mismatch": False, "tier2_blackout": False, "reopen_window": False,
                                      "holiday_thin": False, "calendar_unavailable": False}
UNVERIFIED = "instrument_unverified"   # a BookSpec.blocked reason: registry terms not confirmed (ops.services.engine)
# a BookSpec.blocked reason (trading-safety review of #310, M3): an instrument the account no longer lists while it
# holds positions in its magic range; the book runs their exits and takes no entry (ops.services.engine)
WIND_DOWN = "instrument_wind_down"
TERMS_READ_S = 60.0          # a book's instruments.db row is read again this often (a corruption is seen within it)
NOTIONAL_HEADROOM = 0.95     # the account-wide ceiling keeps 5% under the bridge's (prices move between the two)
# trading-safety review of #310 (M1): a multi-book pass (`account_pass`) runs every book's stop check and close
# retries first, then starts no further book's refresh and decisions once this many seconds (host clock) have passed;
# the books left go first on the next pass. Gold alone never takes that path
PASS_BUDGET_S = 5.0
# live demo issue 2026-11-02 (icm-probe: 19 blocked books on a gold-only bridge, passes up to 35.8 s): a blocked book
# that holds nothing in its magic range is quiet, making no broker call at all (`quiet_books`). Whether it holds
# anything is read from the account's positions at most this often (host clock); its own records are checked every pass
QUIET_RECHECK_S = 10.0
# a non-gold book's symbol_info answer serves its contract, lot terms and terms row this long (host clock;
# `symbol_info`); a failed read is tried again after this long on a blocked book (a symbol the bridge does not serve),
# never once per refresh. Gold's reads are unchanged
SYMBOL_RETRY_S = 1800.0
# a failed symbol_info read on an enabled non-gold book is tried again after this long: its entries wait on it
SYMBOL_RETRY_ENABLED_S = 60.0
# SRE diagnosis 2026-11-09 (fix 4c): gold's key-zones build (display only, 1.2-2.6 s of CPU on its own thread) starts
# once a multi-book pass ran every book and no book has an exploration decision due, so it never competes with the
# bar close's entries; it starts anyway once its inputs have waited this long (host clock)
ZONES_WAIT_S = 60.0
# the pass phases the deferral warning names, in this order (`PassTimes`; fix 4a: "logs per-book phase times")
PHASES: tuple[str, ...] = ("bridge", "bars", "zones", "exploration")
# vantage-demo refusals 2026-11-10 (fix 4: "add sub-phases (learning, step, minutes, enter/_Pass budget reads) to
# PassTimes"): the exploration pass's parts, timed as "exploration:<name>" and shown inside its total
EX_PHASES: tuple[str, ...] = ("reads", "learning", "step", "minutes", "entry", "budget")
# vantage-demo refusals 2026-11-10 (fix 2): a book's 15m+ exploration part (`exploration.on_pass_slow`) waits behind
# pending 1m/5m work at most this long (host clock), so a 1m signal stuck in a queue never holds a close's 15m+ entries
# (60 s stale limit) back; past it, the part runs first
SLOW_WAIT_S = 20.0

T = TypeVar("T")
P = ParamSpec("P")


class BookSpec(FrozenRecord):
    """One non-gold instrument an account's engine trades (from goldbot/instruments.py InstrumentSpec and the account's
    exploration block; built by ops.services.engine)."""
    key: str                         # InstrumentSpec.key
    symbol: str                      # the broker's symbol
    store_symbol: str                # the store's symbol column: never XAUUSD's
    magic_block: int
    digits: int
    point: float
    contract_size: float
    exploration_config: ExplorationConfig | None = None
    # the account's first magic of this instrument (`accounts.instrument_magic_base`); None: `magic.book_base`
    magic_base: int | None = None
    # why this book takes no entry whatever its terms read (`instrument_unverified`: registry terms not confirmed by
    # the symbol check, plan: "never trade a spec with verify: true"); its exits run
    blocked: str | None = None
    # the registry entry (correlation group, leverage class, P&L currency, session profile, news currencies); None only
    # in unit tests, where the book keeps gold's rules
    instrument: InstrumentSpec | None = None


class BookAttr(Generic[T]):
    """A data descriptor: `engine.<name>` is the active book's attribute."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __get__(self, obj: Any, owner: Any = None) -> Any:
        if obj is None:
            return self
        try:
            return obj.__dict__["book"].__dict__[self.name]
        except KeyError:
            raise AttributeError(self.name) from None

    def __set__(self, obj: Any, value: Any) -> None:
        obj.__dict__["book"].__dict__[self.name] = value

    def __delete__(self, obj: Any) -> None:
        del obj.__dict__["book"].__dict__[self.name]


class SymbolBook:
    """One instrument's state. Attributes are the BOOK_ATTRS the engine set on it, plus its own bookkeeping."""

    def __init__(self, cfg: EngineConfig, *, primary: bool, spec: BookSpec | None = None) -> None:
        self.__dict__["cfg"] = cfg
        self.primary = primary
        self.spec = spec
        self.sym_state: dict[str, Any] = dict(FRESH_SYMBOL_STATE)
        self.terms: InstrumentTerms | None = None
        self.terms_problem: str | None = None if primary else "instrument_terms_missing"
        self.terms_read: pd.Timestamp | None = None
        self.terms_written: pd.Timestamp | None = None
        self.fx: dict[tuple[pd.Timestamp, int], float | None] = {}
        self.entry_sessions: Any = None   # E3: the profile-narrowed table entries are judged on (`set_sessions`)
        # (host-clock time, answer or failure) of the last symbol_info read (`symbol_info`, non-gold books)
        self.symbol_read: tuple[float, SymbolInfo | BaseException] | None = None
        # False while a non-gold book waits for its history backfill (engine/book_backfill.py): no entry until then
        self.warm = True

    def __repr__(self) -> str:
        return f"SymbolBook({self.__dict__['cfg'].symbol!r}, primary={self.primary})"


def install(cls: type) -> None:
    """Make every BOOK_ATTRS name of `cls` (the Engine) a BookAttr."""
    for name in BOOK_ATTRS:
        setattr(cls, name, BookAttr(name))


def _switch(eng: Engine, into: SymbolBook) -> None:
    out: SymbolBook = eng.__dict__["book"]
    st = eng.state.__dict__
    out.sym_state = {f: st[f] for f in SYMBOL_STATE}
    st.update(into.sym_state)             # values validated when they were set; no re-validation per switch
    eng.__dict__["book"] = into


@contextmanager
def using(eng: Engine, book: SymbolBook) -> Iterator[SymbolBook]:
    """Run the block on `book` (re-entrant): its attributes and its SYMBOL_STATE are the engine's until the block ends."""
    prev: SymbolBook = eng.__dict__["book"]
    if prev is book:
        yield book
        return
    _switch(eng, book)
    try:
        yield book
    finally:
        _switch(eng, prev)


def problem(book: SymbolBook) -> str | None:
    """Why the book takes no entry now (its `BookSpec.blocked`, else its instruments.db terms), or None."""
    blocked = book.spec.blocked if book.spec is not None else None
    return blocked or book.terms_problem


def warming(eng: Engine) -> bool:
    """True while the active book waits for its warm-up from the broker's history (engine/book_backfill.py): it
    decides nothing and takes no entry; its exits never read this. Gold is always warm."""
    return not eng.__dict__["book"].warm


def multi(eng: Engine) -> bool:
    return len(eng.books) > 1


def is_primary(eng: Engine) -> bool:
    return eng.__dict__["book"] is eng.primary


def book_cfg(cfg: EngineConfig, spec: BookSpec) -> EngineConfig:
    """The book's EngineConfig: the account's, with the instrument's symbol, store symbol, magic range and terms. No
    cost probe, shadow book, broker M1 bars or 24/7 lane (gold-only machinery); its exploration arms its own."""
    return cfg.model_copy(update={
        "symbol": spec.symbol, "release_symbol": spec.store_symbol, "instrument": spec.key,
        "magic_base": spec.magic_base if spec.magic_base is not None else magic.book_base(cfg.magic_base,
                                                                                        spec.magic_block),
        "point": spec.point, "price_digits": spec.digits, "contract_size": spec.contract_size,
        "exploration_config": spec.exploration_config, "cost_probe": False, "shadow_host": False,
        "broker_bars": False, "always_open": False, "pauses": (), "weekend_window": None, "instruments": (),
        **({"weekend_cut": _friday_cut(spec.instrument, cfg.server_tz)} if spec.instrument is not None else {})})


def _friday_cut(spec: InstrumentSpec, server_tz: str) -> str:
    """The instrument's Friday cut (its session profile's `friday_cut`, server time) for the engine's weekend rule."""
    from goldbot.data.calendar import session_profile
    cut = session_profile(spec.session_profile, server_tz).friday_cut
    return f"{cut.hour:02d}:{cut.minute:02d}"


def book_dir(state_dir: str | Path, account_id: str) -> Path:
    return Path(state_dir, "books", account_id)


class BookStore:
    """A non-gold book's state files: its own order rows and open trades (state/books/<account>/orders_<store>.json);
    the risk state is the account's (the primary's file, so a stage change seen on any book is saved at once); the
    engine state file is written by the primary only (one engine_<account>.json per account, gold's view plus `books`)."""

    def __init__(self, account: JsonEngineStore, state_dir: str | Path, account_id: str, store_symbol: str) -> None:
        self.account = account
        d = book_dir(state_dir, account_id)
        d.mkdir(parents=True, exist_ok=True)
        self.orders = JsonEngineStore(d, store_symbol)

    def load_orders(self) -> OrdersSnapshot | None:
        return self.orders.load_orders()

    def save_orders(self, s: OrdersSnapshot) -> None:
        self.orders.save_orders(s)

    def load_risk(self) -> RiskSnapshot | None:
        return self.account.load_risk()

    def save_risk(self, s: RiskSnapshot) -> None:
        self.account.save_risk(s)

    def write_engine_state(self, payload: dict[str, Any], durable: bool = False) -> None:
        return None                       # the primary publishes the account's file (`published`)


def open_books(eng: Engine, specs: tuple[BookSpec, ...]) -> None:
    """Create a book per spec (Engine.__init__, after gold's): its state, its exploration, its order rows reconciled
    with the broker. Demo only: a live or observe-only account never trades a non-gold instrument (plan)."""
    if not specs:
        return
    from goldbot.engine import exploration
    cfg = eng.primary.__dict__["cfg"]
    if cfg.mode != "demo":
        raise ValueError(f"{cfg.account_id}: non-gold instruments run on demo accounts only (mode {cfg.mode})")
    for spec in specs:
        bcfg = book_cfg(cfg, spec)
        if bcfg.symbol in eng.books:
            raise ValueError(f"{cfg.account_id}: symbol {bcfg.symbol} has two books")
        book = SymbolBook(bcfg, primary=False, spec=spec)
        eng.books[bcfg.symbol] = book
        with using(eng, book):
            eng._init_book()
            if spec.instrument is not None:    # its hours until the run loop reads the broker's (plan: per asset class)
                from goldbot.data.calendar import session_profile
                # L2 (review of #310): through set_sessions, so the profile narrows its entries only (E3)
                set_sessions(eng, session_profile(spec.instrument.session_profile, bcfg.server_tz))
            eng.agents, eng.models, eng.live_shares, eng.live_versions = {}, {}, {}, {}
            eng.shadow, eng.shadow_models, eng.probe = None, {}, None
            eng.state_store = BookStore(eng.primary.__dict__["state_store"], bcfg.state_dir, bcfg.account_id,
                                        spec.store_symbol)
            eng.exploration = exploration.Exploration.start(eng)
            eng._kill_pending = eng._kill_everything
            eng._load_orders()
    # L2 (risk): a multi-symbol account's daily limit holds across symbols (RiskGate `multi_symbol`); the services
    # set it in the policy's defaults too, so a limits refresh keeps it
    eng.gate.limits = eng.gate.limits.model_copy(update={"multi_symbol": True})
    on_decision = eng._on_decision                  # proposals are the primary's: applied on its book (its symbol)

    def on_primary(p: Any) -> None:
        with using(eng, eng.primary):
            on_decision(p)
    eng.center.on_decision = on_primary


class QuoteFeed:
    """The pass's quotes for every book in one call (plan: "batched ticks_many(symbols, since) every 500 ms"): the
    newest tick of each symbol from `Broker.ticks_many`, else (an adapter or bridge that does not serve it yet, or a
    read that failed) that symbol's own `last_tick`. A symbol with no new tick keeps its last quote, so the engine's
    clock and repeat rule see what one `last_tick` poll would. Read-only; `last_tick` raises as the broker's does."""

    def __init__(self, broker: Any) -> None:
        self.broker = broker
        self.latest: dict[str, Tick] = {}
        self.batched = True

    def refresh(self, symbols: list[str], now: pd.Timestamp) -> None:
        if not self.batched:
            return
        since = min((t.ts_utc for s, t in self.latest.items() if s in symbols), default=now - pd.Timedelta(minutes=1))
        try:
            got = self.broker.ticks_many(symbols, since)
        except NotImplementedError:
            self.batched = False             # this adapter or bridge has no batch read: one last_tick per symbol
            log.info("ticks_many not served: one last_tick per symbol and pass")
            return
        except Exception as exc:              # a failed batch: each symbol's own read this pass
            log.warning("ticks_many failed (%s): one last_tick per symbol this pass", type(exc).__name__)
            self.latest = {}
            return
        for s, ticks in got.items():
            if ticks:
                self.latest[s] = max(ticks, key=lambda t: t.ts_utc)

    def last_tick(self, symbol: str) -> Tick:
        if self.batched and symbol in self.latest:
            return self.latest[symbol]
        return self.broker.last_tick(symbol)


class PassQuotes:
    """The pass's quote per symbol, read once (`last_tick`, a raise included) and served to both of the pass's phases,
    so the exits phase costs no extra bridge call."""

    def __init__(self, source: Any) -> None:
        self.source = source
        self.got: dict[str, Tick | BaseException] = {}

    def last_tick(self, symbol: str) -> Tick:
        if symbol not in self.got:
            try:
                self.got[symbol] = self.source.last_tick(symbol)
            except Exception as exc:
                self.got[symbol] = exc
        v = self.got[symbol]
        if isinstance(v, BaseException):
            raise v
        return v


# what the pass serves once to every book (trading-safety review of #310, M1); any order call clears them
PASS_READS = frozenset({"account", "account_positions"})
PASS_WRITES = frozenset({"place_order", "close", "modify"})
# the broker's figures asked once a pass per exact question (live 2026-11-04, brain py-spy of icm-probe: 9 s in 150 s
# of passes were `loss_per_lot` round trips, every book's exploration pass asking the loss at the stop of the same
# open positions again). A conversion of the arguments given, no position in it: an order call does not clear it
PASS_FIGURES = frozenset({"loss_per_lot"})


class PassBroker:
    """The account's broker for one multi-book pass (M1: "read account() and account_positions() ONCE per pass and
    share them across books"): each of PASS_READS is read once and the same answer (or the same exception: a failing
    bridge is not asked 26 times) is served to every book; an order, close or modify sent through it clears them, so
    no book decides on positions read before an order of this pass. Each of PASS_FIGURES is asked once a pass per
    exact arguments (the same answer or exception for the same question). Everything else goes straight to the
    broker. With `times` (the pass's PassTimes) every call that reaches the broker is timed as the `bridge` phase."""

    def __init__(self, broker: Any, times: PassTimes | None = None) -> None:
        self.__dict__["_inner"] = broker
        self.__dict__["_memo"] = {}
        self.__dict__["_figures"] = {}
        self.__dict__["_times"] = times

    def __getattr__(self, name: str) -> Any:
        inner, memo = self.__dict__["_inner"], self.__dict__["_memo"]
        raw = getattr(inner, name)
        times: PassTimes | None = self.__dict__["_times"]
        attr = raw if times is None or not callable(raw) else _bridge_timed(times, raw)
        if name in PASS_FIGURES:
            figures = self.__dict__["_figures"]

            def figure(*a: Any) -> Any:
                key = (name, *a)
                if key not in figures:
                    try:
                        figures[key] = attr(*a)
                    except Exception as exc:
                        figures[key] = exc
                v = figures[key]
                if isinstance(v, BaseException):
                    raise v
                return v
            return figure
        if name in PASS_READS:
            def read() -> Any:
                if name not in memo:
                    try:
                        memo[name] = attr()
                    except Exception as exc:
                        memo[name] = exc
                v = memo[name]
                if isinstance(v, BaseException):
                    raise v
                return list(v) if isinstance(v, list) else v
            return read
        if name in PASS_WRITES:
            def write(*a: Any, **k: Any) -> Any:
                memo.clear()
                return attr(*a, **k)
            return write
        return attr

    def clear(self) -> None:
        """Forget the pass's reads: the next one goes to the broker (an order sent around this wrapper, `book`)."""
        self.__dict__["_memo"].clear()


class PassTimes:
    """Where one multi-book pass went, by book and phase (SRE diagnosis 2026-11-09, fix 4a: "logs per-book phase times
    (bridge, frame, exploration) in the deferral warning"): `bridge` every broker call through the pass's PassBroker,
    `bars` the bar close (frames, features, decisions), `zones` gold's key zones at the close, `exploration` the
    exploration pass. Self time: a phase inside another (a bridge call inside the bar close) counts in the inner one
    only, so a book's phases never add up to more than its pass. `book` is the book being timed ("" for the pass's
    shared steps: quotes, exits). Measurement only: nothing reads it to decide anything; a call from another thread
    (a worker holding the pass's broker) is not timed."""

    def __init__(self) -> None:
        self.book = ""
        self.by: dict[str, dict[str, float]] = {}
        self._inner: list[float] = []            # the open phases' time spent in phases inside them
        self._thread = threading.get_ident()

    @contextmanager
    def phase(self, name: str) -> Iterator[None]:
        if threading.get_ident() != self._thread:
            yield
            return
        start = time.monotonic()
        self._inner.append(0.0)
        try:
            yield
        finally:
            took = time.monotonic() - start
            mine = took - self._inner.pop()
            row = self.by.setdefault(self.book, {})
            row[name] = row.get(name, 0.0) + mine
            if self._inner:
                self._inner[-1] += took

    def describe(self, book: str) -> str:
        return self._text(self.by.get(book, {}))

    def totals(self) -> str:
        """Every book's phases added up (the pass's shared steps left out: they are logged on their own)."""
        names = {k for r in self.by.values() for k in r}
        return self._text({k: sum(r.get(k, 0.0) for b, r in self.by.items() if b) for k in names})

    @staticmethod
    def _text(row: dict[str, float]) -> str:
        """Each phase's time; exploration's with its parts (EX_PHASES) inside it, those that took any time listed."""
        parts = {p: row.get(f"exploration:{p}", 0.0) for p in EX_PHASES}
        out = []
        for p in PHASES:
            v = row.get(p, 0.0) + (sum(parts.values()) if p == "exploration" else 0.0)
            inner = ", ".join(f"{k} {t:.2f}" for k, t in parts.items() if t >= 0.005) if p == "exploration" else ""
            out.append(f"{p} {v:.2f}" + (f" [{inner}]" if inner else ""))
        return ", ".join(out)


def _bridge_timed(times: PassTimes, fn: Callable[..., Any]) -> Callable[..., Any]:
    def call(*a: Any, **k: Any) -> Any:
        with times.phase("bridge"):
            return fn(*a, **k)
    return call


def phase(eng: Engine, name: str) -> Any:
    """The block timed as `name` in the running account pass's PassTimes; a no-op outside one (gold alone, replays)."""
    times: PassTimes | None = eng.__dict__.get("_pass_times")
    return times.phase(name) if times is not None else nullcontext()


def timed(eng: Engine, name: str, fn: Callable[P, T], *a: P.args, **k: P.kwargs) -> T:
    """`fn(*a, **k)` timed as the phase `name` (`phase`); its result or its exception, unchanged."""
    with phase(eng, name):
        return fn(*a, **k)


def exploration_pass(eng: Engine, tick: Tick) -> None:
    """The engine pass's last entry step: `exploration.on_pass` (timed `exploration`), then, on an engine with one
    book, gold's key-zones build parked at this pass's bar close starts (`key_zones.launch`): after the entries were
    evaluated, never during them (fix 4c; a multi-book account starts it after every book, `account_pass`)."""
    from goldbot.engine import exploration, key_zones
    timed(eng, "exploration", exploration.on_pass, eng, tick)
    if not multi(eng):
        key_zones.launch(eng)


def _launch_zones(eng: Engine, deferred: bool, active: list[SymbolBook], now: pd.Timestamp) -> None:
    """Fix 4c: start gold's parked key-zones build once this pass ran every book and none has an exploration decision
    due, or once its inputs have waited ZONES_WAIT_S. Display only; never raises."""
    from goldbot.engine import key_zones
    try:
        parked = eng.key_zone_worker.parked
        if parked is None:
            return
        at = time.monotonic()
        seen = eng.__dict__.get("_zones_seen")
        if seen is None or seen[0] != parked[1]:
            seen = eng.__dict__["_zones_seen"] = (parked[1], at)
        if (not deferred and not any(_is_due(b, now) for b in active)) or at - seen[1] >= ZONES_WAIT_S:
            key_zones.launch(eng)
    except Exception:              # noqa: BLE001 - display only: the pass and every exit go on
        log.exception("key zones build not started; tried again next pass")


def _pass_broker(broker: Any, times: PassTimes | None = None) -> PassBroker:
    """A PassBroker whose class carries the broker's class flags (`balance_exact`, read on the type: capital_flows)."""
    cls = type(broker)
    sub = _PASS_TYPES.get(cls)
    if sub is None:
        sub = _PASS_TYPES[cls] = type(f"Pass{cls.__name__}", (PassBroker,),
                                      {"balance_exact": getattr(cls, "balance_exact", False)})
    return sub(broker, times)


_PASS_TYPES: dict[type, type[PassBroker]] = {}


def _exits_first(eng: Engine, quotes: PassQuotes, symbol: str) -> bool:
    """The active book's stop check and close retries on this pass's quote (`Engine._stop_check`,
    `Engine._retry_closes`), before any book's refresh or decisions. A missing or unusable quote skips them here: the
    book's own pass then runs `no_quote_pass` (its close retries) as before. False when one raised (a tick error)."""
    try:
        t = quotes.last_tick(symbol)
    except Exception:
        return True
    if eng._quote_fault(t) is not None:
        return True
    try:
        eng._stop_check(t)
        eng._retry_closes(t.ts_utc)
    except Exception as exc:
        eng.note_tick_error(exc)
        return False
    return True


def _quiet_ok(b: SymbolBook) -> bool:
    """A blocked non-gold book with no record of its own to work on: no open trade, close due, kill pending, or
    pending_orders row still `sending` or ambiguous (its reconciliation must run)."""
    from goldbot.engine import unconfirmed
    d = b.__dict__
    pending = any(o.status in ("sending", unconfirmed.AMBIGUOUS) for o in (d.get("_orders") or {}).values())
    return (not b.primary and b.spec is not None and b.spec.blocked is not None and not d.get("open")
            and not d.get("_close_due") and not d.get("_kill_pending") and not pending)


def _in_range(b: SymbolBook, m: int) -> bool:
    base = b.__dict__["cfg"].magic_base
    return bool(base <= m < base + 100)            # Engine._own: the book's 100 magics


def quiet_books(eng: Engine) -> set[str]:
    """The books that make no broker call this pass (live demo issue 2026-11-02: "a book that is blocked and holds NO
    open position in its magic range makes ZERO broker calls per pass ... It publishes its state as disabled from
    config alone"). A book is quiet when its `BookSpec.blocked` is set (`instrument_unverified`: disabled, verify, or a
    symbol the terminal could not describe at start, so one the bridge does not serve; `instrument_wind_down`), it
    holds no record of its own (`_quiet_ok`) and the account's positions show none on its symbol inside its magic
    range. The positions come from the pass's shared account_positions() read, at most every QUIET_RECHECK_S; a read
    that fails keeps the last successful answer, and with none yet no book is quiet (fail toward running the exits).
    A manual trade on the symbol (outside the range) wakes nothing: the book would never touch it. A book that is not
    quiet runs its whole pass, exits included, as before."""
    candidates = [b for b in eng.books.values() if _quiet_ok(b)]
    if not candidates:
        return set()
    now = time.monotonic()
    at, held = eng.__dict__.get("_quiet_held", (None, None))
    if at is None or now - at >= QUIET_RECHECK_S:
        try:
            every = list(eng.broker.account_positions())
            held = frozenset(b.__dict__["cfg"].symbol for b in candidates
                             if any(p.symbol == b.__dict__["cfg"].symbol and _in_range(b, p.magic) for p in every))
        except Exception as exc:
            log.warning("account positions unreadable (%s): the blocked books' quiet check keeps its last answer",
                        type(exc).__name__)
        eng.__dict__["_quiet_held"] = (now, held)
    if held is None:                               # never read successfully: no book is known to hold nothing
        return set()
    quiet = {b.__dict__["cfg"].symbol for b in candidates} - held
    if quiet != eng.__dict__.get("_quiet_logged"):
        eng.__dict__["_quiet_logged"] = quiet
        log.info("%s: %d blocked book(s) holding nothing make no broker call: %s",
                 eng.primary.__dict__["cfg"].account_id, len(quiet), ", ".join(sorted(quiet)) or "none")
    return quiet


def symbol_info(eng: Engine) -> SymbolInfo:
    """The active book's symbol_info outside the decision cycle (its contract size, the ladder's lot terms, its
    instruments.db row). Gold: read now, as before. Another book: one answer serves every use for SYMBOL_RETRY_S; a
    failure is raised again for SYMBOL_RETRY_S on a blocked book (a symbol the bridge does not serve is never asked
    once per refresh: live demo issue 2026-11-02, "symbol terms are read at most once per symbol per N minutes") and
    for SYMBOL_RETRY_ENABLED_S on an enabled one, whose entries wait on it."""
    book: SymbolBook = eng.__dict__["book"]
    if book.primary:
        return eng.broker.symbol_info(eng.cfg.symbol)
    now = time.monotonic()
    blocked = book.spec is not None and book.spec.blocked is not None
    keep = SYMBOL_RETRY_S if book.symbol_read is None or not isinstance(book.symbol_read[1], BaseException) \
        or blocked else SYMBOL_RETRY_ENABLED_S
    if book.symbol_read is None or now - book.symbol_read[0] >= keep:
        try:
            got: SymbolInfo | BaseException = eng.broker.symbol_info(eng.cfg.symbol)
        except Exception as exc:
            got = exc
        book.symbol_read = (now, got)
    v = book.symbol_read[1]
    if isinstance(v, BaseException):
        raise v
    return v


def _is_due(book: SymbolBook, now: pd.Timestamp) -> bool:
    """Ordering hint only: a failure here must never stop the exits that follow (trading-safety L1, 2026-11-03)."""
    from goldbot.engine import exploration
    try:
        return bool(exploration.due(book.__dict__.get("exploration"), now))
    except Exception:
        log.exception("exploration.due failed for %s; book keeps its rotation place", book.__dict__["cfg"].symbol)
        return False


def account_pass(eng: Engine, broker: Any, one_pass: Any, feed: QuoteFeed | None = None,
                 budget_s: float = PASS_BUDGET_S) -> bool:
    """One loop pass over every book: `one_pass(eng, quotes, symbol)` (ops.services.engine.engine_pass) on each book
    in turn, serially, the quotes read once for all (`QuoteFeed`, `PassQuotes`). True when every book's pass was clean.

    Trading-safety review of #310 (M1, bridge load): "read account() and account_positions() ONCE per pass and share
    them across books" (`PassBroker`, installed as the engine's broker for the pass); "run every book's _stop_check and
    _retry_closes before any book's refresh or decisions" (`_exits_first`, every book, every pass); "bound each pass
    with a deadline": once `budget_s` (PASS_BUDGET_S) has passed no further book starts its pass, and the books left go first on
    the next one (after gold, whose pass polls the approvals). Gold and the next book in turn always run, so none
    starves however slow the bridge. Exits never wait on the deadline.

    Live demo issue 2026-11-03 (icm-probe, 20 books: the 1m S0 controls fire on every book at once and the later
    books' decisions passed their 10 s staleness): the books with an exploration decision due (`exploration.due`) run
    right after gold, the rest after them, each group in the fair rotation; the rotation's next book in turn runs
    even past the deadline, so due books never starve the others.

    A quiet book (`quiet_books`: blocked, holding nothing) takes no part at all: no quote, no exits step, no pass.

    SRE diagnosis 2026-11-09 (fix 4a): the deferral warning names the quotes read and, for the slowest books, their
    bridge, bar close, key zones and exploration time (`PassTimes`), with every book's phases added up. Fix 4c: gold's
    key-zones build parked at its bar close starts after the books' passes (`_launch_zones`).

    Vantage-demo refusals 2026-11-10 (SRE diagnosis, fixes 2 and 3: "on a 1m boundary run a cheap sub-15m entries
    sweep across all due books before any book's 15m+ step"): each book's pass runs only its exploration's 1m/5m part
    (`exploration.SPLIT`); the 15m+ parts run after every book's pass (`_slow_parts`), so no 1m signal waits for
    another book's close step."""
    from goldbot.engine import exploration
    ok = True
    t0 = time.monotonic()
    cfg = eng.primary.__dict__["cfg"]
    prev = eng.broker
    times = PassTimes()
    eng.__dict__["_pass_times"] = times
    eng.__dict__["broker"] = _pass_broker(prev, times)
    eng.__dict__[exploration.SPLIT] = exploration.SplitPass(_bridge_timed(times, lambda s: broker.last_tick(s)),
                                                             eng._auto_paused)
    deferred = False
    try:
        quiet = quiet_books(eng)
        active = [b for b in eng.books.values() if b.__dict__["cfg"].symbol not in quiet]
        for b in active:                             # owed by this pass only: never a tick of an earlier one
            if getattr(b.__dict__.get("exploration"), "slow_owed", None) is not None:
                b.__dict__["exploration"].slow_owed = None
        t_quotes = time.monotonic()
        if feed is not None:
            feed.refresh([b.__dict__["cfg"].symbol for b in active], pd.Timestamp.now("UTC"))
        t_quotes = time.monotonic() - t_quotes
        quotes = PassQuotes(feed if feed is not None else broker)
        rest = [b for b in active if b is not eng.primary]
        start = eng.__dict__.get("_pass_cursor", 0) % len(rest) if rest else 0
        turn = [*rest[start:], *rest[:start]]
        now = eng.clock()
        due = [b for b in turn if _is_due(b, now)]
        order = [eng.primary, *due, *(b for b in turn if b not in due)]
        t_exits = time.monotonic()
        for book in order:
            with using(eng, book):
                ok = _exits_first(eng, quotes, book.__dict__["cfg"].symbol) and ok
        t_exits = time.monotonic() - t_exits
        left: list[SymbolBook] = []
        took: dict[str, float] = {}
        for i, book in enumerate(order):
            if (left or (i > 1 and time.monotonic() - t0 >= budget_s)) and not (turn and book is turn[0]):
                left.append(book)
                continue
            t_book = time.monotonic()
            times.book = book.__dict__["cfg"].symbol
            try:
                with using(eng, book):
                    ok = bool(one_pass(eng, quotes, book.__dict__["cfg"].symbol)) and ok
            finally:
                times.book = ""                  # a pass that raised never has later time charged to its book
            took[book.__dict__["cfg"].symbol] = time.monotonic() - t_book
        carried = _slow_parts(eng, [b for b in order if b not in left], active, t0, budget_s, times, took)
        if left:
            eng.__dict__["_pass_cursor"] = rest.index(next(b for b in turn if b in left))
            eng.__dict__["_passes_deferred"] = eng.__dict__.get("_passes_deferred", 0) + 1
        if left or carried:
            if not eng.__dict__.get("_pass_was_deferred"):      # logged once per run of deferred passes
                slow = ", ".join(f"{s} {t:.2f} s ({times.describe(s)})"
                                 for s, t in sorted(took.items(), key=lambda x: -x[1])[:3])
                log.warning("%s: pass budget %.1f s spent after %d of %d books (%d due first); the rest go first "
                            "next pass; 15m+ work carried for %d books; quotes %.2f s; exits %.2f s; phases: %s; "
                            "slowest: %s", cfg.account_id, budget_s, len(order) - len(left), len(order), len(due),
                            carried, t_quotes, t_exits, times.totals(), slow)
            deferred = True
        _launch_zones(eng, deferred, active, now)
    finally:
        eng.__dict__["_pass_was_deferred"] = deferred
        eng.__dict__["broker"] = prev
        eng.__dict__.pop("_pass_times", None)
        eng.__dict__.pop(exploration.SPLIT, None)
    return ok


def _slow_parts(eng: Engine, ran: list[SymbolBook], active: list[SymbolBook], t0: float, budget_s: float,
                times: PassTimes, took: dict[str, float]) -> int:
    """The 15m+ exploration parts this pass's books owe (`exploration.on_pass_slow`, timed `exploration`), after every
    book's pass: the books carried from earlier passes first, in the order they were carried, then this pass's order.
    None starts while 1m/5m work is pending on a book whose 1m/5m part ran this pass (`exploration.fast_pending`: a
    signal queued, a minute due or inside its grace; vantage-demo refusals 2026-11-10 fix 3; a book whose pass never
    reached its exploration, no quote, holds nobody) unless it has waited SLOW_WAIT_S, and after the first
    none starts past the budget. A part not run is carried: the book's next pass owes it again (its close step and
    learning read are state-driven, so nothing is lost). The number of books carried."""
    from goldbot.engine import exploration
    carry: dict[str, float] = eng.__dict__.setdefault("_slow_carry", {})   # symbol -> host clock it was first carried
    for gone in set(carry) - {str(b.__dict__["cfg"].symbol) for b in active}:
        del carry[gone]                             # code review LOW 1: a book gone quiet owes nothing
    rank = {s: i for i, s in enumerate(carry)}

    def sym(b: SymbolBook) -> str:
        return str(b.__dict__["cfg"].symbol)
    owed = []
    for b in ran:
        if getattr(b.__dict__.get("exploration"), "slow_owed", None) is not None:
            owed.append(b)
        else:
            carry.pop(sym(b), None)                 # nothing owed (no exploration, no quote, a part that raised)
    owed.sort(key=lambda b: rank.get(sym(b), len(rank)))
    n = 0
    for book in owed:
        at, s = time.monotonic(), sym(book)
        overdue = s in carry and at - carry[s] >= SLOW_WAIT_S
        if (n and at - t0 >= budget_s) or (not overdue and _fast_pending(eng, owed)):
            carry.setdefault(s, at)
            book.__dict__["exploration"].slow_owed = None
            continue
        carry.pop(s, None)
        n += 1
        times.book = s
        try:
            with using(eng, book):
                t_book = time.monotonic()
                timed(eng, "exploration", exploration.on_pass_slow, eng)
                took[s] = took.get(s, 0.0) + time.monotonic() - t_book
        finally:
            times.book = ""
    return len(carry)


def _fast_pending(eng: Engine, owed: list[SymbolBook]) -> bool:
    """Any book's 1m/5m exploration work is pending (`exploration.fast_pending`) on the host clock now. Ordering hint
    only: a failure reads as none pending (trading-safety L1, 2026-11-03)."""
    from goldbot.engine import exploration
    now = eng.clock()
    for b in owed:
        try:
            if exploration.fast_pending(b.__dict__.get("exploration"), now):
                return True
        except Exception:
            log.exception("exploration.fast_pending failed for %s; read as nothing pending", b.__dict__["cfg"].symbol)
    return False


def positions(eng: Engine) -> list[Any]:
    """The active book's broker positions: gold's call is unchanged (the adapter's own symbol); a non-gold book's are
    the account's positions on its symbol (`Broker.account_positions`, read once per pass for every book: live demo
    issue 2026-11-02, never one bridge call per book)."""
    if is_primary(eng):
        return list(eng.broker.positions())
    return [p for p in eng.broker.account_positions() if p.symbol == eng.cfg.symbol]


AWAIT_REFRESH_S = 60.0   # a booked fill not seen by a refresh within this is left to reconciliation (it closed fast)


def unconfirmed(eng: Engine) -> bool:
    """BACKLOG 72 across the account: an unsettled order row on ANY book refuses every entry; on a multi-instrument
    account so does a booked fill no refresh has seen yet (E2: "keep unconfirmed_order set until a refresh sees the
    position"), for at most AWAIT_REFRESH_S."""
    from goldbot.engine import unconfirmed as unc
    if any(unc.pending(b.__dict__.get("_orders", {})) for b in eng.books.values()):
        return True
    awaited: dict[int, pd.Timestamp] = eng.__dict__.get("_awaited", {})
    return bool(awaited)


def costs_file(eng: Engine) -> Path:
    """The cost table the nightly job writes for the active book: gold's costs_<account>.json; a non-gold book's own
    file under state/books/<account>/ (absent until a job measures it: the configured cost then applies)."""
    cfg = eng.cfg
    if is_primary(eng):
        return Path(cfg.state_dir, f"costs_{cfg.account_id}.json")
    return book_dir(cfg.state_dir, cfg.account_id) / f"costs_{cfg.release_symbol}.json"


def set_sessions(eng: Engine, table: Any) -> None:
    """E3 (trading-safety review of #307): a non-gold book's exits, bar ingestion and management read the broker's own
    hours (the table with its profile intersection off); its session profile narrows ENTRIES only (`entry_sessions`,
    applied in `after_refresh`). A table from the static fallback is the profile itself (E5: never DEFAULT_SESSIONS).
    Gold: the table as given. L2 (trading-safety review of #310): the book's own current table handed back (a failed
    refresh: `SessionRefresher.collect` returns it) changes nothing, so it never drops the profile narrowing entries."""
    book: SymbolBook = eng.__dict__["book"]
    if not book.primary and table is eng.sessions:
        return
    if book.primary or not getattr(table, "intersect_broker", False):
        eng.sessions = table
        book.entry_sessions = None
        return
    book.entry_sessions = table
    eng.sessions = table.model_copy(update={"intersect_broker": False})


def feature_ctx(eng: Engine) -> dict[str, Any] | None:
    """The feature context of the active book: None for gold (every build as before); `{"symbol": key}` for another
    instrument, so gold's dollar features (round numbers, ...) are off for it (features/survey.py, lane L1)."""
    return None if is_primary(eng) else {"symbol": eng.cfg.instrument}


def store_symbol(eng: Engine) -> str:
    """The store's symbol for the active book's rows (ticks, decisions, fills, exit legs, dq events): gold keeps its
    traded symbol as today; another book its unique `store_symbol`, so nothing is ever pooled with XAUUSD."""
    return eng.cfg.symbol if is_primary(eng) else eng.cfg.release_symbol


def live_key(eng: Engine) -> tuple[str, str]:
    """(source, symbol) of the active book's `bars_1m_live` minutes. Plan: "store bars_1m_live keyed (source=account,
    symbol=store_symbol), never pooled with XAUUSD" for a non-gold book; gold keeps (broker name, its symbol), the
    history it has read and written since ADR 0062 ("XAUUSD paths byte-identical")."""
    cfg = eng.cfg
    return (cfg.broker_name, cfg.symbol) if is_primary(eng) else (cfg.account_id, cfg.release_symbol)


# ------------------------------------------------------------------ instruments.db terms (non-gold books)
def refresh_terms(eng: Engine, now: pd.Timestamp, db: InstrumentsDb | None = None, tick: Tick | None = None) -> None:
    """A non-gold book's terms: written from symbol_info every broker_terms_every_s (and first thing), read back every
    TERMS_READ_S. Sets the book's `terms_problem` (None, `instruments_db_unreadable`, `instrument_terms_missing`,
    `swap_unknown`), which refuses its entries; never raises. The price a swap needs (mode 2/3 in the base currency,
    modes 5/6 interest) is the bid of `tick`, the book's tick this refresh runs on, else the book's last accepted tick:
    point in time, never a later price. Without one the write (6 h cadence) would leave the swap unknown that long."""
    book: SymbolBook = eng.__dict__["book"]
    if book.primary:
        return
    db = db or instruments_db(eng)
    cfg = eng.cfg
    if book.terms_written is None or (now - book.terms_written).total_seconds() >= cfg.broker_terms_every_s:
        book.terms_written = now
        try:
            info = symbol_info(eng)               # shared with the contract and lot terms reads
            tick = tick if tick is not None else book.__dict__.get("_last_tick")   # x price: modes 2/3 base, 5/6
            inst = book.spec.instrument if book.spec is not None else None   # F5: x price for fx/metal pairs only
            db.save(terms_from_info(cfg.account_id, cfg.symbol, cfg.release_symbol, info, now,
                                    price=None if tick is None else getattr(tick, "bid", None),
                                    group=inst.correlation_group if inst is not None else None))
        except InstrumentsDbError as exc:
            log.error("%s %s: %s; its entries are refused, exits run", cfg.account_id, cfg.symbol, exc)
        except Exception as exc:          # the terminal: the row last written (if any) stands
            log.warning("%s %s: symbol_info unavailable for instruments.db (%s)", cfg.account_id, cfg.symbol,
                        type(exc).__name__)
        book.terms_read = None            # read back what was written
    if book.terms_read is not None and (now - book.terms_read).total_seconds() < TERMS_READ_S:
        return
    book.terms_read = now
    try:
        book.terms = db.load(cfg.account_id, cfg.symbol)
    except InstrumentsDbError as exc:
        if book.terms_problem != "instruments_db_unreadable":
            log.error("%s %s: %s; its entries are refused, exits run", cfg.account_id, cfg.symbol, exc)
        book.terms, book.terms_problem = None, "instruments_db_unreadable"
        return
    t = book.terms
    book.terms_problem = ("instrument_terms_missing" if t is None
                          else "swap_unknown" if t.swap_long_per_lot is None or t.swap_short_per_lot is None
                          or t.swap_triple_weekday is None or not 1 <= t.swap_triple_weekday <= 5 else None)


def instruments_db(eng: Engine) -> InstrumentsDb:
    db = eng.__dict__.get("_instruments_db")
    if db is None:
        db = eng.__dict__["_instruments_db"] = InstrumentsDb(eng.cfg.state_dir)
    return db


def swap_spec(eng: Engine) -> SwapSpec | None:
    """The active non-gold book's swap for the RiskGate's daily projection, per unit of its contract; None when
    unknown (the caller prices NaN, which the gate refuses)."""
    from goldbot.labels.triple_barrier import SwapSpec
    book: SymbolBook = eng.__dict__["book"]
    t = book.terms
    if book.terms_problem is not None or t is None or t.swap_long_per_lot is None or t.swap_short_per_lot is None \
            or t.swap_triple_weekday is None:
        return None
    return SwapSpec(long_usd_per_lot=t.swap_long_per_lot, short_usd_per_lot=t.swap_short_per_lot,
                    triple_weekday=t.swap_triple_weekday - 1, server_tz=eng.cfg.server_tz,
                    contract_oz=t.contract_size)


# ------------------------------------------------------------------ the serial gate and the instrument's rules
def check(eng: Engine, intent: Intent, now: pd.Timestamp, **kw: Any) -> GateDecision:
    """`RiskGate.check` on the account's one AccountState (trading-safety review of #307, E1): the account's figures,
    bookings included, plus the active book's own SYMBOL_STATE, never a copy. On a non-gold book the spread is held to
    the instrument's own cap (InstrumentSpec `max_spread_points`, lane L1; gold's 45 points mean nothing on another
    point size): none configured refuses (`spread_cap_missing`). Gold: exactly `gate.check`.
    E5 (trading-safety review of #307): the intent must name the instrument this book sends to the broker
    (`Intent.symbol`, from `intent_terms`; an intent with no terms reads XAUUSD): any other is refused
    (`symbol_mismatch`) before the gate, so an order is never sized on one instrument's terms and sent on another."""
    from goldbot.risk.gate import GateDecision
    book: SymbolBook = eng.__dict__["book"]
    sent = book.__dict__["cfg"].instrument
    if getattr(intent, "symbol", GOLD) != sent:
        log.error("%s: intent for %s on the %s book refused (symbol_mismatch)", eng.cfg.account_id,
                  getattr(intent, "symbol", GOLD), sent)
        return GateDecision(allowed=False, reasons=["symbol_mismatch"])
    if book.primary:
        return ceiling(eng, intent, eng.gate.check(intent, eng.state, now, **kw))
    spec = book.spec.instrument if book.spec is not None else None
    cap = getattr(spec, "max_spread_points", None) if spec is not None else eng.gate.limits.max_spread_points
    if cap is None:
        return GateDecision(allowed=False, reasons=["spread_cap_missing"])
    limits = eng.gate.limits
    eng.gate.limits = limits.model_copy(update={"max_spread_points": float(cap)})
    try:
        gd = eng.gate.check(intent, eng.state, now, **kw)
    finally:
        eng.gate.limits = limits
    return ceiling(eng, intent, gd)


def ceiling(eng: Engine, intent: Intent, gd: GateDecision) -> GateDecision:
    """The bridge's own ceiling, account-wide (execution/bridge.py `MAX_NOTIONAL_EQUITY_MULT`, 6x equity over every
    symbol): the gate's per-group caps add up to far more (FX alone is 9x), so on a multi-instrument account an
    allowed entry that would take the account's notional (every group, account currency) past the bridge's multiple
    is refused here (`account_notional_cap`), never sent for the bridge to refuse. Only ever refuses; gold alone:
    unchanged. Unknown exposure is the gate's `exposure_unknown`."""
    from goldbot.execution.bridge import MAX_NOTIONAL_EQUITY_MULT
    from goldbot.risk.gate import GateDecision, profit_fx
    st = eng.state
    if not multi(eng) or not gd.allowed or st.group_notional is None:
        return gd
    fx = profit_fx(intent)
    held = sum(st.group_notional.values())
    add = gd.lots * intent.contract_oz * intent.price * (fx if fx is not None else math.inf)
    if not math.isfinite(add) or held + add > MAX_NOTIONAL_EQUITY_MULT * st.equity * NOTIONAL_HEADROOM:
        return GateDecision(allowed=False, reasons=["account_notional_cap"])
    return gd


def book(eng: Engine, intent: Intent, gd: GateDecision, res: Any = True) -> None:
    """E2 (trading-safety review of #307): an entry is booked on the shared state (`RiskGate.book`) right after its
    order went out (decision time on demo, after the approval-time re-check otherwise; never at proposal time), so
    the next symbol's check on the same bar counts it until a refresh reads the broker's positions. `res` None: the
    order was not sent (a duplicate), nothing booked. Gold alone: nothing (its refresh after the order stands).
    Inside a multi-book pass the pass's shared account and position reads are cleared (`PassBroker.clear`): an entry
    sent on the bridge's order thread (not through the PassBroker), even one answered with no position id, is never
    left out of a later book's refresh in the same pass."""
    if multi(eng) and res is not None:
        if isinstance(eng.broker, PassBroker):
            eng.broker.clear()
        eng.gate.book(eng.state, intent, gd)
        pid = getattr(res, "position_id", None)
        if pid is not None:               # E2: entries wait until a refresh sees it (`unconfirmed`, `account_exposure`)
            eng.__dict__.setdefault("_awaited", {})[int(pid)] = pd.Timestamp.now("UTC")
        eng.state.unconfirmed_order = True


def intent_extra(eng: Engine, terms: dict[str, Any]) -> dict[str, Any]:
    """The Intent fields of the active non-gold book's instrument (`gate.intent_terms`) and its account currency per
    unit of P&L currency (`acct_per_profit`: the broker's conversion `_sizing_terms` read as `acct_per_usd`, which for
    a non-USD quote is per unit of that quote). Gold: {} (the Intent defaults are gold's)."""
    from goldbot.risk.gate import intent_terms
    book: SymbolBook = eng.__dict__["book"]
    spec = book.spec.instrument if book.spec is not None else None
    if book.primary or spec is None:
        return {}
    return {**intent_terms(spec), "acct_per_profit": terms.get("acct_per_usd")}   # E5: intent_terms carries symbol


def after_refresh(eng: Engine, tick: Tick, now: pd.Timestamp) -> None:
    """`Engine._refresh_account`'s multi-book step (no-op with gold alone), after the engine's own rules ran on the
    active book: a non-gold book's terms (a problem refuses its entries: `dq_error`), its instrument's news blackout
    (`instrument_blackout`: its currencies and its group's commodity reports; an unscheduled shock still counts) and
    its Friday cut (`windows.weekend_cut` on its session profile); then the account's exposure over every book."""
    if not multi(eng):
        return
    book: SymbolBook = eng.__dict__["book"]
    st = eng.state
    if not book.primary:
        if book.spec is None or book.spec.blocked is None:   # a blocked book takes no entry: its terms are never read
            refresh_terms(eng, tick.ts_utc, tick=tick)
        if problem(book) is not None:
            st.dq_error = True
        spec = book.spec.instrument if book.spec is not None else None
        if spec is not None:
            from goldbot.data.news import instrument_blackout
            from goldbot.risk.windows import weekend_cut
            cfg = eng.cfg
            if cfg.news_blackout:
                ev = eng._calendar[1] if eng.store is not None else None
                shock = eng._blackout_event is not None and eng._blackout_event.get("kind") == "news_shock"
                hit = instrument_blackout(ev, tick.ts_utc, spec.news_currencies, cfg.blackout_before_min,
                                          cfg.blackout_after_min, group=spec.correlation_group)
                # L2 (#307 M5): any hit blocks, `calendar_unavailable` included; a shock is always a shock
                st.in_blackout = shock or hit is not None
                st.blackout_kind = "news_shock" if shock else (hit.get("kind") or "tier1") if hit else None
            try:
                if book.entry_sessions is not None and book.entry_sessions.entry_block(now) is not None:
                    st.market_closed = True     # E3: the profile closes entries; exits never read it
                st.weekend = st.weekend or weekend_cut(eng.sessions, now)
            except Exception as exc:          # a table that cannot say: closed for entries (fail closed)
                log.warning("%s: Friday cut unknown (%s); entries refused", cfg.symbol, type(exc).__name__)
                st.weekend = True
    account_exposure(eng, tick.ts_utc)


def account_exposure(eng: Engine, now: pd.Timestamp) -> None:
    """Every position on every book's symbol counts (plan: account-wide heat), from one `account_positions()` read,
    each valued on its own book's quote, contract and the broker's conversion (`open_risk.SymbolQuote`): the open risk
    at the stops (`account_open_risk`), the notional per correlation group and the gold lots (`group_exposure`), and
    the other accounts' groups (`supervisor.others_group_notional`). Anything the engine cannot value makes the figure
    unknown: open risk None (RiskGate refuses while the heat cap or the projection is on) and `exposure_unknown`
    (every entry refused). Exits never read them."""
    from goldbot.risk.open_risk import SymbolQuote, account_open_risk, group_exposure
    st = eng.state
    books = {b.__dict__["cfg"].symbol: b for b in eng.books.values()}
    try:
        every = list(eng.broker.account_positions())
    except Exception as exc:
        log.warning("account positions unreadable (%s): exposure unknown, entries refused", type(exc).__name__)
        st.open_risk, st.exposure_unknown = None, True
        return
    held = [p for p in every if p.symbol in books]
    # M3 (trading-safety review of #310): "any position whose symbol has no book sets exposure_unknown and raises a
    # health alert": its exposure counts in no group, so every entry is refused while it stays open (published as
    # `unbooked_positions`, health's symbols check FAILs). The positions themselves are never touched here
    unbooked = [p for p in every if p.symbol not in books]
    if {p.position_id for p in unbooked} != {p.position_id for p in eng.__dict__.get("_unbooked", [])} and unbooked:
        log.error("%s: %d position(s) on symbols no book trades (%s): exposure unknown, every entry refused",
                  eng.cfg.account_id, len(unbooked), ", ".join(sorted({p.symbol for p in unbooked})))
    eng.__dict__["_unbooked"] = unbooked
    quotes: dict[str, SymbolQuote] = {}
    for sym, b in books.items():
        tick, contract = b.__dict__.get("_last_tick"), b.__dict__.get("_contract")
        if tick is None or contract is None or not any(p.symbol == sym for p in held):
            continue
        fx = _profit_fx(eng, b, 1, tick.ask, contract, now)
        group = b.spec.instrument.correlation_group if b.spec is not None and b.spec.instrument is not None else "gold"
        quotes[sym] = SymbolQuote(bid=tick.bid, ask=tick.ask, contract=contract, fx=fx, group=group)
    awaited: dict[int, pd.Timestamp] = eng.__dict__.get("_awaited", {})
    if awaited:                           # E2: a booked fill this read sees (or too old to wait for) is settled
        seen, wall = {p.position_id for p in held}, pd.Timestamp.now("UTC")
        eng.__dict__["_awaited"] = {k: t for k, t in awaited.items()
                                    if k not in seen and (wall - t).total_seconds() < AWAIT_REFRESH_S}
        st.unconfirmed_order = unconfirmed(eng)
    eng.__dict__["_held"] = held           # the dashboard's lots and positions per group (`published`)
    st.open_positions = len(held)
    st.open_long = sum(1 for p in held if p.side > 0)
    st.open_short = sum(1 for p in held if p.side < 0)
    st.open_risk, eng._open_risk_rows = account_open_risk(held, quotes)
    groups, gold_lots = group_exposure(held, quotes)
    others = _others(eng)
    st.exposure_unknown = groups is None or others is None or bool(unbooked)
    # E4 (trading-safety review of #307): open_lots and open_notional are the gold group's (lots, USD); every group's
    # notional is in the deposit currency; unknown is None, published as null so the supervisor halts
    st.group_notional = groups
    if groups is not None:
        st.open_lots = round(gold_lots, 6)
        usd = _usd_fx(eng, now)
        if usd is not None:
            st.open_notional = groups.get("gold", 0.0) / usd
        else:
            st.exposure_unknown = True
    # L2: on a multi-symbol state the other accounts' map is always a map; unknown is {} with exposure_unknown set
    st.other_group_notional = others if others is not None else {}


def _others(eng: Engine) -> dict[str, float] | None:
    """The other real accounts' notional per group in this account's currency ({} without halt checks: tests)."""
    if not eng.cfg.halt_checks:
        return {}
    from goldbot.risk.supervisor import Supervisor, others_group_notional
    try:
        return others_group_notional(Supervisor.read_state(eng.cfg.state_dir), eng.cfg.account_id,
                                     currency=eng._account_currency(), acct_per_usd=_usd_fx(eng, pd.Timestamp.now("UTC")))
    except Exception as exc:
        log.warning("other accounts' group notional unreadable (%s): exposure unknown", type(exc).__name__)
        return None


def _profit_fx(eng: Engine, b: SymbolBook, side: int, px: float, contract: float, now: pd.Timestamp) -> float | None:
    """Account currency per unit of `b`'s profit currency, from the broker's loss on one lot (`loss_per_lot`), read at
    most once a minute per side. None when the broker cannot say (never 1 by assumption)."""
    key = (now.floor("min"), 1 if side > 0 else -1)
    if key in b.fx:
        return b.fx[key]
    fn = getattr(eng.broker, "loss_per_lot", None)
    dist = max(abs(px) * 0.01, b.__dict__["cfg"].point)
    out: float | None = None
    try:
        loss = fn(b.__dict__["cfg"].symbol, side, px, dist) if callable(fn) else None
        v = float(loss) / (dist * contract) if loss is not None else math.nan
        out = v if math.isfinite(v) and v > 0 else None
    except Exception as exc:
        log.warning("broker conversion unavailable for %s (%s)", b.__dict__["cfg"].symbol, type(exc).__name__)
    b.fx = {k: v for k, v in b.fx.items() if k[0] == key[0]}
    b.fx[key] = out
    return out


def _usd_fx(eng: Engine, now: pd.Timestamp) -> float | None:
    """Account currency per USD, from the primary (gold, a USD-profit symbol) book's conversion; 1 on a USD account."""
    from goldbot.risk import sizing
    p = eng.primary
    tick, contract = p.__dict__.get("_last_tick"), p.__dict__.get("_contract")
    currency = eng._account_currency()
    if currency.strip().upper() == "USD":
        return 1.0
    if tick is None or contract is None:
        return None
    return sizing.account_fx(currency, _profit_fx(eng, p, 1, tick.ask, contract, now))


STATE_TEXT = {"can_enter": "can enter", "market_closed": "market closed", "stale": "no fresh prices",
              "group_cap": "group exposure cap reached", "terms_missing": "terms not recorded",
              "disabled": "terms not confirmed: no new trades",
              # incident 2026-11-02: terms present, but a swap the engine cannot price (`swap_unknown`)
              "swap_unknown": "swap not priced: entries refused, exits still close"}
WIND_DOWN_TEXT = "winding down: no new trades, open ones keep their exits"


def _symbol_state(eng: Engine, b: SymbolBook, s: dict[str, Any]) -> str:
    """The dashboard's state of one book (goldbot/api/instruments_view.py InstrumentState), first that applies."""
    why = problem(b)
    if why in (UNVERIFIED, WIND_DOWN):
        return "disabled"
    if why == "swap_unknown":
        return "swap_unknown"
    if why is not None:
        return "terms_missing"
    if s.get("market_closed") or s.get("weekend"):
        return "market_closed"
    if s.get("stale_bars") or s.get("dq_error"):
        return "stale"
    group = b.spec.instrument.correlation_group if b.spec is not None and b.spec.instrument is not None else "gold"
    cap = _group_cap(eng, group, b)
    held = (eng.state.group_notional or {}).get(group, 0.0)
    return "group_cap" if cap is not None and held >= cap else "can_enter"


def _group_cap(eng: Engine, group: str, b: SymbolBook | None) -> float | None:
    """The gate's notional cap of `group` (account currency), as `_sized_limits` computes it; None when unknown."""
    st, L = eng.state, eng.gate.limits
    spec = b.spec.instrument if b is not None and b.spec is not None else None
    try:
        lev = eng.gate.leverage(st, spec.leverage_class if spec is not None else "gold_index", group)
        return float(L.max_combined_notional_frac * lev * (st.equity + st.other_equity))
    except Exception:                         # noqa: BLE001 - display only
        return None


def published(eng: Engine) -> dict[str, Any]:
    """Multi-book only, for health and the dashboard (lane L4's contract, goldbot/api/instruments_view.py): `books`
    (each instrument's own view), `symbols` {symbol: state, text, clears_at, price, lot_value}, `groups` {group:
    notional, cap, lots_by_symbol, open_n}, `heat_used` / `heat_limit` (the loss at every stop, its limit) and the
    account's `group_notional` (account currency; the supervisor's per-group caps), null when unknown so the
    supervisor halts (E4). M2 (trading-safety review of #310): a probe_only engine publishes {} with
    `exposure_unknown` instead, never null: the supervisor keeps probe files out of every combined sum, but reads a
    null map as an unreadable file and would halt the real accounts on a probe's unknown. M3: `unbooked_positions`,
    the account's positions on a symbol no book trades (health FAILs on them). Reporting only: no entry or exit reads
    it."""
    if not multi(eng):
        return {}
    st = eng.state
    out: dict[str, Any] = {}
    symbols: dict[str, Any] = {}
    for sym, b in eng.books.items():
        d, s = b.__dict__, (st.__dict__ if b is eng.__dict__["book"] else b.sym_state)
        last = d.get("_last_tick")
        out[sym] = {"instrument": d["cfg"].instrument, "store_symbol": d["cfg"].release_symbol,
                    "magic_base": d["cfg"].magic_base, "primary": b.primary, "price": d.get("_price"),
                    "last_tick_utc": last.ts_utc.isoformat() if last is not None else None,
                    "open": sorted(d.get("open", {})), "closes_due": sorted(d.get("_close_due", {})),
                    "foreign_positions": d.get("_foreign", []), "terms_problem": problem(b),
                    "warm_up": (eng.__dict__["_book_backfill"].status.get(sym, {"warm": b.warm})
                                if "_book_backfill" in eng.__dict__ else {"warm": b.warm}),
                    **{k: s.get(k) for k in ("spread_points", "last_tick_age_s", "market_closed", "stale_bars",
                                             "dq_error", "in_blackout", "weekend")}}
        state = _symbol_state(eng, b, s)
        fx = next((v for (_, side), v in sorted(b.fx.items(), key=lambda kv: kv[0][0]) if side == 1), None)
        contract = d.get("_contract")
        text = WIND_DOWN_TEXT if problem(b) == WIND_DOWN else STATE_TEXT[state]
        symbols[sym] = {"state": state, "text": text, "clears_at": None, "price": d.get("_price"),
                        "lot_value": fx * contract if fx is not None and contract is not None else None}
    groups: dict[str, Any] = {}
    by_symbol = {b.__dict__["cfg"].symbol: b for b in eng.books.values()}
    for p in eng.__dict__.get("_held", []):
        bk = by_symbol.get(p.symbol)
        g = bk.spec.instrument.correlation_group if bk is not None and bk.spec is not None \
            and bk.spec.instrument is not None else "gold"
        row = groups.setdefault(g, {"notional": (st.group_notional or {}).get(g), "cap": _group_cap(eng, g, bk),
                                    "lots_by_symbol": {}, "open_n": 0})
        row["lots_by_symbol"][p.symbol] = round(row["lots_by_symbol"].get(p.symbol, 0.0) + p.lots, 6)
        row["open_n"] += 1
    heat = eng.gate.limits.heat
    gn: dict[str, float] | None = None
    unknown = st.exposure_unknown
    if not st.exposure_unknown and st.group_notional is not None:
        gn = dict(st.group_notional)
    elif eng.cfg.probe_only:                  # M2: never null on a probe file; the flag says it is unknown
        gn, unknown = {}, True
    return {"books": out, "symbols": symbols, "groups": groups, "heat_used": st.open_risk,
            "heat_limit": heat.max_open_risk * st.equity if heat.enabled else None,
            "exposure_unknown": unknown, "group_notional": gn,
            "unbooked_positions": [{"position_id": p.position_id, "symbol": p.symbol, "magic": p.magic}
                                   for p in eng.__dict__.get("_unbooked", [])],
            "passes_deferred": eng.__dict__.get("_passes_deferred", 0)}
