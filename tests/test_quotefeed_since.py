"""QuoteFeed's `since` (incident 2026-11-18/19, vantage-demo): one closed symbol (USINDEX, no tick 21:59:59..01:00:01
UTC) held the batched read's `since` at its last tick, the bridge's `copy_ticks_from(since, 2000)` returned gold's
OLDEST 2,000 ticks after the reopen, and the engine re-read a gold quote up to 105 min old (software stops and trails
ran on it; at 01:00 the catch-up replayed backdated ticks). Design rule: a closed or quiet symbol never holds back
another symbol's ticks, and the feed never serves a backlog tick: the pass's quote is each symbol's newest."""
from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from goldbot.engine import books
from goldbot.execution.broker import Tick
from goldbot.execution.mt5_adapter import TICKS_MANY_MAX

T = pd.Timestamp("2026-11-18 23:00:00", tz="UTC")


class TerminalLike:
    """Every tick each symbol has, served as the MT5 adapter does: `ticks_many` gives at most TICKS_MANY_MAX ticks
    strictly after `since`, OLDEST first (copy_ticks_from); `last_tick` the newest at or before `now`."""

    def __init__(self) -> None:
        self.ticks: dict[str, list[Tick]] = {}
        self.now = T
        self.sinces: list[pd.Timestamp] = []
        self.last_tick_calls: list[str] = []

    def add(self, symbol: str, start: pd.Timestamp, n: int, step_s: float, bid: float) -> None:
        self.ticks.setdefault(symbol, []).extend(
            Tick(ts_utc=start + pd.Timedelta(seconds=i * step_s), bid=bid + i * 0.01, ask=bid + i * 0.01 + 0.2)
            for i in range(n))

    def ticks_many(self, symbols: Sequence[str], since: pd.Timestamp) -> dict[str, list[Tick]]:
        self.sinces.append(since)
        return {s: [t for t in self.ticks.get(s, []) if since < t.ts_utc <= self.now][:TICKS_MANY_MAX]
                for s in symbols}

    def last_tick(self, symbol: str) -> Tick:
        self.last_tick_calls.append(symbol)
        return [t for t in self.ticks[symbol] if t.ts_utc <= self.now][-1]

    def newest(self, symbol: str) -> Tick:
        return [t for t in self.ticks[symbol] if t.ts_utc <= self.now][-1]


def _overnight() -> TerminalLike:
    term = TerminalLike()
    term.add("USINDEX", pd.Timestamp("2026-11-18 21:59:59", tz="UTC"), 1, 1.0, 20000.0)   # closed until 01:00:01
    term.add("XAUUSD", T, 2 * 3600 * 10, 0.1, 4000.0)                                       # gold: 10 ticks a second
    return term


def test_a_closed_symbol_never_holds_gold_back_when_gold_ticks_more_than_one_batch_holds():
    term = _overnight()
    feed = books.QuoteFeed(term)
    feed.latest = {"USINDEX": term.ticks["USINDEX"][0]}   # the index's last tick before its close
    for minute in range(1, 31):                           # gold reopens; 600 ticks a minute, 18,000 by 23:30
        term.now = T + pd.Timedelta(minutes=minute)
        feed.refresh(["XAUUSD", "USINDEX"], term.now)
        got = feed.last_tick("XAUUSD")
        assert got == term.newest("XAUUSD"), (minute, got.ts_utc)
        assert (term.now - got.ts_utc).total_seconds() < 1
    assert feed.last_tick("USINDEX").ts_utc == pd.Timestamp("2026-11-18 21:59:59", tz="UTC")   # closed: kept


def test_since_follows_the_newest_symbol_less_the_stale_limit_not_the_closed_symbols_last_tick():
    term = _overnight()
    feed = books.QuoteFeed(term, stale_s=20)
    term.now = T + pd.Timedelta(minutes=20)
    feed.latest = {"USINDEX": term.ticks["USINDEX"][0], "XAUUSD": term.newest("XAUUSD")}
    feed.refresh(["XAUUSD", "USINDEX"], term.now)
    assert term.sinces[-1] == term.newest("XAUUSD").ts_utc - pd.Timedelta(seconds=20)


def test_since_is_unchanged_while_every_symbol_ticked_within_the_stale_limit():
    term = TerminalLike()
    term.add("XAUUSD", T, 100, 0.1, 4000.0)
    term.add("EURUSD", T, 20, 0.5, 1.1)
    term.now = T + pd.Timedelta(seconds=10)
    feed = books.QuoteFeed(term)
    feed.latest = {"XAUUSD": term.newest("XAUUSD"), "EURUSD": term.ticks["EURUSD"][10]}
    feed.refresh(["XAUUSD", "EURUSD"], term.now)
    assert term.sinces[-1] == term.ticks["EURUSD"][10].ts_utc                 # the oldest latest tick, as before
    assert feed.last_tick("EURUSD") == term.newest("EURUSD")


def test_a_symbol_stamped_in_the_future_never_pushes_since_past_the_clock():
    term = _overnight()
    term.now = T + pd.Timedelta(minutes=5)
    feed = books.QuoteFeed(term, stale_s=20)
    bogus = Tick(ts_utc=T + pd.Timedelta(hours=3), bid=1.0, ask=1.1)
    feed.latest = {"XAUUSD": term.newest("XAUUSD"), "USINDEX": bogus}
    held = term.newest("XAUUSD")
    term.now += pd.Timedelta(seconds=1)
    feed.refresh(["XAUUSD", "USINDEX"], term.now)
    assert term.sinces[-1] <= held.ts_utc
    assert feed.last_tick("XAUUSD") == term.newest("XAUUSD")


def test_a_full_batch_is_a_backlog_and_the_feed_serves_the_terminals_newest_quote_instead():
    term = TerminalLike()
    term.add("XAUUSD", T, 6000, 0.1, 4000.0)                # 10 min of gold the brain did not read (a paused host)
    term.now = T + pd.Timedelta(seconds=600)
    feed = books.QuoteFeed(term)
    feed.latest = {"XAUUSD": term.ticks["XAUUSD"][0]}
    feed.refresh(["XAUUSD"], term.now)
    assert feed.last_tick("XAUUSD") == term.newest("XAUUSD")    # never the 2,000th tick of the backlog
    assert term.last_tick_calls == ["XAUUSD"]


def test_new_ticks_older_than_the_stale_limit_are_a_replay_and_never_become_the_quote():
    term = TerminalLike()
    term.add("XAUJPY", T, 5, 1.0, 600000.0)                 # five ticks delivered now, stamped 10 min ago
    term.add("XAUJPY", T + pd.Timedelta(seconds=599), 1, 1.0, 600100.0)
    term.now = T + pd.Timedelta(seconds=600)
    feed = books.QuoteFeed(term, stale_s=20)

    def late(symbols: Sequence[str], since: pd.Timestamp) -> dict[str, list[Tick]]:
        return {"XAUJPY": term.ticks["XAUJPY"][:5]}         # the backdated part of a catch-up
    term.ticks_many = late                                  # type: ignore[method-assign]
    feed.refresh(["XAUJPY"], term.now)
    assert feed.last_tick("XAUJPY") == term.newest("XAUJPY")


def test_a_batch_never_moves_a_symbols_quote_back_in_time():
    term = TerminalLike()
    term.add("XAUUSD", T, 50, 0.1, 4000.0)
    term.now = T + pd.Timedelta(seconds=5)
    feed = books.QuoteFeed(term)
    feed.latest = {"XAUUSD": term.newest("XAUUSD")}

    def older(symbols: Sequence[str], since: pd.Timestamp) -> dict[str, list[Tick]]:
        return {"XAUUSD": term.ticks["XAUUSD"][10:20]}
    term.ticks_many = older                                 # type: ignore[method-assign]
    feed.refresh(["XAUUSD"], term.now)
    assert feed.last_tick("XAUUSD") == term.newest("XAUUSD")


def test_a_closed_symbol_that_reopens_is_served_its_first_tick():
    term = _overnight()
    term.add("USINDEX", pd.Timestamp("2026-11-19 01:00:01", tz="UTC"), 3, 0.5, 20010.0)
    feed = books.QuoteFeed(term)
    feed.latest = {"USINDEX": term.ticks["USINDEX"][0]}
    for sec in range(0, 4):
        term.now = pd.Timestamp("2026-11-19 01:00:00", tz="UTC") + pd.Timedelta(seconds=sec * 0.5)
        feed.latest["XAUUSD"] = term.newest("XAUUSD")    # gold read every pass up to here
        feed.refresh(["XAUUSD", "USINDEX"], term.now)
    assert feed.last_tick("USINDEX") == term.newest("USINDEX")
    assert feed.last_tick("USINDEX").ts_utc > pd.Timestamp("2026-11-19 01:00:00", tz="UTC")
    assert term.last_tick_calls == []                   # the batch alone served it


def test_when_the_backlog_symbols_newest_quote_cannot_be_read_the_pass_reads_it_itself():
    term = TerminalLike()
    term.add("XAUUSD", T, 6000, 0.1, 4000.0)
    term.now = T + pd.Timedelta(seconds=600)
    feed = books.QuoteFeed(term)
    feed.latest = {"XAUUSD": term.ticks["XAUUSD"][0]}

    def down(symbol: str) -> Tick:
        raise ConnectionError("bridge")
    term.last_tick = down                                   # type: ignore[method-assign]
    feed.refresh(["XAUUSD"], term.now)
    assert "XAUUSD" not in feed.latest                      # never the backlog tick: the pass's own read raises
