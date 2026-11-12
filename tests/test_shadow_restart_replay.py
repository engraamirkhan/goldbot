"""Backlog 165: shadow trades open across an engine restart are replayed through the bars missed while it was down.

Design rule (engine/shadow.py): "trade mechanics mirror the triple-barrier labels the models were trained on, so shadow
and backtest are comparable". The shadow book advances only on the bars the engine closes, so a trade open across a
restart skipped the downtime (backlog 161 tagged it `restart_gap`). At the engine's start a trade with a resume point
is held; at a base close where the history covers its missed span it is advanced through them, point in time, exactly as
`on_bar` would have; a trade the history never covers within REPLAY_WAIT, or one with no resume point, keeps
`restart_gap`, which calibration and shadow-vs-live reads exclude. Shadow only: no order is ever sent."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from goldbot.data.resample import resample_bars, ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.engine import Engine, EngineConfig, shadow_replay
from goldbot.engine.history import HistoryGap
from goldbot.engine.shadow import ShadowBook, ShadowTrade, minutes_of
from goldbot.execution.broker import Tick
from goldbot.execution.paper import PaperBroker
from goldbot.risk.throttle import latest_shadow_win
from goldbot.telegram.approvals import ApprovalCenter

OWNER = 111
B1 = ticks_to_1m(synthetic_ticks("2025-03-04", "2025-03-06", ticks_per_minute=2, seed=11))
B15 = resample_bars(B1, "15m")
ENTRY = 20                    # the signal bar (row of B15); the trade enters at its close
DOWN = 30                     # the last bar the engine advanced the trade on before it died
RESTART = 42                  # the bar the first close after the restart completes (its visible_at is that close)


def _open(book: ShadowBook, stop_atr: float, target_atr: float, on_1m: bool) -> ShadowTrade:
    bar = B15.iloc[ENTRY]
    book.track("v1", pd.Timestamp(bar["ts_utc"]))
    t = book.open_trade(version="v1", agent_id="a", side=1, bar_ts=pd.Timestamp(bar["ts_utc"]),
                        entry=float(bar["ask_close"]), atr_usd=2.0, target_atr=target_atr, stop_atr=stop_atr,
                        max_bars=40, p=0.6, signal_close=pd.Timestamp(bar["visible_at"]), barriers_on_1m=on_1m)
    assert t is not None
    return t


def _advance(book: ShadowBook, rows: range) -> None:
    for i in rows:
        bar = B15.iloc[i]
        book.on_bar(bar, "15m", minutes_of(B1, bar, "15m"))


def _engine(state_dir: Path) -> Engine:
    return Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(state_dir), owner_user_id=OWNER,
                               shadow_host=True), PaperBroker(equity=10_000), [], {}, ApprovalCenter({OWNER}))


def _died_at(state_dir: Path, stop_atr: float = 50.0, target_atr: float = 50.0, on_1m: bool = False) -> None:
    """The host engine's shadow book: a trade opened at ENTRY's close, advanced through DOWN, saved; then the engine
    died."""
    book = ShadowBook(state_dir, evidence_start=None)
    _open(book, stop_atr, target_atr, on_1m)
    _advance(book, range(ENTRY + 1, DOWN + 1))
    book.save(pd.Timestamp(B15["visible_at"].iloc[DOWN]))


def _at_close(eng: Engine, row: int) -> tuple[pd.DataFrame, pd.Timestamp]:
    """The engine at the close of B15 row `row`: the 1m bars it decides on (warm-start history and live minutes)."""
    close = pd.Timestamp(B15["visible_at"].iloc[row])
    eng.bars_1m = B1[B1["visible_at"] <= close].reset_index(drop=True)
    return eng.bars_1m, close


def _trade(book: ShadowBook) -> ShadowTrade:
    [t] = [*book.books["v1"].open, *book.books["v1"].closed]
    return t


# a time exit after the restart, and a target hit inside the downtime (16 bars in), on decision bars and on 1m bars
@pytest.mark.parametrize("stop_atr,target_atr,on_1m", [(50.0, 50.0, False), (20.0, 30.0, False), (20.0, 30.0, True)])
def test_a_shadow_trade_replayed_across_a_restart_ends_identical_to_one_that_never_restarted(
        tmp_path, stop_atr, target_atr, on_1m):
    ref = ShadowBook(tmp_path / "ref", evidence_start=None)
    _open(ref, stop_atr, target_atr, on_1m)
    _advance(ref, range(ENTRY + 1, len(B15)))
    _died_at(tmp_path / "host", stop_atr, target_atr, on_1m)
    eng = _engine(tmp_path / "host")
    assert eng.shadow is not None
    [held] = eng.shadow.held()
    assert not held.restart_gap
    complete, close = _at_close(eng, RESTART)
    shadow_replay.catch_up(eng, complete, close, [])
    assert not eng.shadow.held()
    _advance(eng.shadow, range(RESTART, len(B15)))          # the normal path from the restart's first close on
    assert _trade(eng.shadow).model_dump() == _trade(ref).model_dump()
    assert _trade(eng.shadow).exit is not None              # each case runs to its exit


def test_a_held_trade_is_not_advanced_by_the_bars_closing_before_its_replay(tmp_path):
    _died_at(tmp_path)
    eng = _engine(tmp_path)
    assert eng.shadow is not None
    _advance(eng.shadow, range(RESTART, RESTART + 1))
    assert _trade(eng.shadow).bars_held == DOWN - ENTRY


def test_a_trade_whose_missed_bars_the_history_has_not_covered_stays_held_then_is_tagged_restart_gap(tmp_path):
    _died_at(tmp_path)
    eng = _engine(tmp_path)
    assert eng.shadow is not None
    complete, close = _at_close(eng, RESTART)
    hole = HistoryGap(start=pd.Timestamp(B15["ts_utc"].iloc[DOWN + 2]), end=pd.Timestamp(B15["ts_utc"].iloc[DOWN + 5]),
                      open_min=45)
    shadow_replay.catch_up(eng, complete, close, [hole])
    assert eng.shadow.held() and _trade(eng.shadow).bars_held == DOWN - ENTRY     # waits for the fill
    shadow_replay.catch_up(eng, complete, close + shadow_replay.REPLAY_WAIT, [hole])
    t = _trade(eng.shadow)
    assert not eng.shadow.held() and t.restart_gap and t.bars_held == DOWN - ENTRY
    _advance(eng.shadow, range(RESTART + 1, RESTART + 2))  # advances from the next bar, as before
    assert t.bars_held == DOWN - ENTRY + 1


def test_a_trade_whose_window_starts_after_its_last_bar_is_not_replayed(tmp_path):
    _died_at(tmp_path)
    eng = _engine(tmp_path)
    assert eng.shadow is not None
    complete, close = _at_close(eng, RESTART)
    late = complete[complete["ts_utc"] > pd.Timestamp(B15["ts_utc"].iloc[DOWN + 1])]
    shadow_replay.catch_up(eng, late, close, [])
    assert eng.shadow.held()


def test_a_trade_saved_without_a_resume_point_is_tagged_restart_gap_at_the_engines_start(tmp_path):
    """A book saved before `last_bar_ts` existed: a trade that advanced has no known last bar, so it cannot be replayed
    and is tagged (backlog 161); one already tagged by an earlier restart stays tagged and is not held."""
    _died_at(tmp_path)
    book = ShadowBook(tmp_path, evidence_start=None)
    _trade(book).last_bar_ts = None
    book.save(pd.Timestamp(B15["visible_at"].iloc[DOWN]))
    host = ShadowBook(tmp_path, host=True)
    assert not host.held() and _trade(host).restart_gap
    host.save(pd.Timestamp(B15["visible_at"].iloc[DOWN]))
    again = ShadowBook(tmp_path, host=True)
    assert not again.held() and _trade(again).restart_gap


def test_a_reader_that_opens_the_book_holds_and_tags_nothing(tmp_path):
    _died_at(tmp_path)
    book = ShadowBook(tmp_path)
    assert not book.held() and not _trade(book).restart_gap


def test_a_trade_held_when_the_engine_dies_again_is_held_and_replayed_at_the_next_start(tmp_path):
    """Held is never persisted as a gap: the trade is saved as it was, so the next start replays both downtimes."""
    ref = ShadowBook(tmp_path / "ref", evidence_start=None)
    _open(ref, 50.0, 50.0, False)
    _advance(ref, range(ENTRY + 1, len(B15)))
    _died_at(tmp_path / "host")
    first = _engine(tmp_path / "host")
    assert first.shadow is not None
    first.shadow.save(pd.Timestamp(B15["visible_at"].iloc[RESTART]))
    second = _engine(tmp_path / "host")
    assert second.shadow is not None and second.shadow.held()
    complete, close = _at_close(second, RESTART + 4)
    shadow_replay.catch_up(second, complete, close, [])
    _advance(second.shadow, range(RESTART + 4, len(B15)))
    assert _trade(second.shadow).model_dump() == _trade(ref).model_dump()


def _gap_book(tmp_path: Path) -> ShadowBook:
    book = ShadowBook(tmp_path, evidence_start=None)
    t0 = pd.Timestamp("2025-03-05 10:00", tz="UTC")
    book.track("v1", t0)
    for k, gap in enumerate((False, True)):
        book.books["v1"].closed.append(ShadowTrade(
            version="v1", agent_id="a", side=1, entry_ts=t0 + pd.Timedelta(hours=k), entry=2400.0, stop=2398.0,
            target=2403.0, max_bars=4, p=0.6, threshold=0.5, exit_ts=t0 + pd.Timedelta(hours=k, minutes=30),
            exit=2403.0, barrier="target", ret=3.0 / 2400.0, restart_gap=gap))
    return book


def test_restart_gap_trades_are_left_out_of_promotion_calibration_rearm_and_throttle_reads(tmp_path):
    book = _gap_book(tmp_path)
    t0 = pd.Timestamp("2025-03-05 10:00", tz="UTC")
    assert book.stats("v1", t0 + pd.Timedelta(days=7)).n_trades == 1
    assert [t.restart_gap for t in book.outcomes("v1")] == [False]
    assert len(book.returns_since("v1", t0)) == 1
    win = latest_shadow_win(book, "a", "v1")
    assert win is not None and not win.restart_gap        # the later win has a gap: the earlier one is the latest


def test_the_engines_first_bar_close_after_a_restart_replays_the_held_trades(tmp_path):
    """Wiring: `Engine.on_bar_close` runs the catch-up before its timeframes advance their shadow trades (above the
    frame floor: 150 decision bars in the window); the trade's 41-bar time exit fell inside the downtime."""
    _died_at(tmp_path)
    eng = _engine(tmp_path)
    assert eng.shadow is not None
    _, close = _at_close(eng, 150)
    px = float(B1["bid_close"].iloc[-1])
    eng.on_bar_close(close, Tick(ts_utc=close + pd.Timedelta(seconds=2), bid=px, ask=px + 0.2))
    t = _trade(eng.shadow)
    assert not eng.shadow.held() and t.barrier == "time" and t.bars_held == 41 and not t.restart_gap
