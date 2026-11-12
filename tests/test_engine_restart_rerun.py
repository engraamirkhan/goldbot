"""Backlog 163: a restart never decides a bar close the previous engine already processed.

Design rule (D10, `Engine._due_close`): "each close is processed once, whichever path sees it first". Before, the
record of the last processed close (`_closed_through`) lived in memory only, so a restart whose first quote was stamped
before that close closed the bar again and ran its entry decisions again: a signal RiskGate refused at the original
close (spread, heat, budget) could pass at a later price (a stale entry; sent ids stop only duplicates). The last
processed base close is now saved per book with the orders file at every close and restored at start; an older file
without it starts as before."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from goldbot.engine import Engine
from goldbot.engine.runner import QUOTE_DEAD_S
from goldbot.engine.store import OrdersSnapshot
from goldbot.execution.broker import Tick
from tests.test_engine_downtime_catch_up import M15, _counted_trade, _engine, _feed
from tests.test_exploration_engine import CLOSE, _arm
from tests.test_exploration_engine import _engine as _exploration_engine


def _orders(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "orders_icm-demo.json").read_text())


def _spy(eng: Engine) -> list[pd.Timestamp]:
    """The closes the engine decides (`on_bar_close` runs), in order."""
    seen: list[pd.Timestamp] = []
    decide = eng.on_bar_close

    def spy(close_ts: pd.Timestamp, last_tick: Tick) -> list[dict]:
        seen.append(close_ts)
        return decide(close_ts, last_tick)
    eng.on_bar_close = spy  # type: ignore[method-assign]
    return seen


def test_the_last_processed_close_is_saved_with_the_orders_file_at_every_close(tmp_path):
    pb, _, last = _counted_trade(tmp_path)
    assert _orders(tmp_path)["closed_through"] == last.isoformat()


def test_a_restart_restores_the_last_processed_close_and_journals_it(tmp_path):
    pb, _, last = _counted_trade(tmp_path)
    eng = _engine(tmp_path, pb)
    assert eng._closed_through == last
    [row] = [d for d in eng.decisions if d.get("action") == "restart_closed_through"]
    assert row["close"] == last.isoformat()


def test_a_restart_whose_first_quote_is_stamped_before_the_last_processed_close_never_decides_that_bar_again(tmp_path):
    pb, pid, last = _counted_trade(tmp_path)
    eng = _engine(tmp_path, pb)
    seen = _spy(eng)
    _feed(eng, pb, last - pd.Timedelta(minutes=2), 2)        # the terminal's stale quotes, in the processed bar
    _feed(eng, pb, last, 1)                                  # the old engine re-closed 1h45 here and decided it again
    assert seen == []
    _feed(eng, pb, last + pd.Timedelta(minutes=1), 15)       # the next close is decided, once
    assert seen == [last + M15]
    assert eng.open[pid].bars_held == 4


def test_one_stale_quote_after_a_restart_is_a_late_tick_that_never_blocks_entries(tmp_path):
    """The stale quote is stamped before the restored close: kept out of the bars and counted (`late_ticks`), but one
    late bar is not LATE_BARS_BLOCK of them in a row, so entries are not blocked."""
    pb, _, last = _counted_trade(tmp_path)
    eng = _engine(tmp_path, pb)
    _feed(eng, pb, last - pd.Timedelta(minutes=2), 2)
    assert eng._late_ticks == 2
    assert "late_ticks" not in eng._dq_blocking()
    _feed(eng, pb, last, 16)                                 # through the next close
    assert "late_ticks" not in eng._dq_blocking()


def test_an_orders_file_saved_before_the_value_existed_starts_as_before(tmp_path):
    """ADR 0010: an older file has no `closed_through`; the restart then knows no processed close, as before."""
    pb, _, last = _counted_trade(tmp_path)
    saved = _orders(tmp_path)
    del saved["closed_through"]
    (tmp_path / "orders_icm-demo.json").write_text(json.dumps(saved))
    eng = _engine(tmp_path, pb)
    assert eng._closed_through is None
    assert not [d for d in eng.decisions if d.get("action") == "restart_closed_through"]
    seen = _spy(eng)
    _feed(eng, pb, last - pd.Timedelta(minutes=2), 3)        # re-closes 1h45, as before this change
    assert seen == [last]


def test_an_engine_that_never_closed_a_bar_writes_no_closed_through(tmp_path):
    """Additive: written only once a close was processed, so an untouched orders file reads exactly as before."""
    pb, _, _ = _counted_trade(tmp_path / "x")
    eng = _engine(tmp_path, pb)
    eng._save_orders()
    assert "closed_through" not in _orders(tmp_path)


def test_a_restart_never_runs_the_exploration_step_again_for_the_restored_close(tmp_path):
    """The exploration step runs once per close it has not seen (`last_close`); a restart starts it at the restored
    close, so the arms' entries for a processed close are not decided again either."""
    eng, _ = _exploration_engine(tmp_path, [_arm("x1")])
    assert eng.exploration is not None and eng.exploration.last_close is None
    eng._closed_through = CLOSE
    eng._save_orders()
    again, _ = _exploration_engine(tmp_path, [_arm("x1")])
    assert again.exploration is not None and again.exploration.last_close == CLOSE


def test_the_dead_feed_barrier_of_a_trade_without_bars_through_counts_from_its_entry_not_the_restored_close(tmp_path):
    """Trading-safety review S-2: a trade with no `bars_through` (an orphan adopted at start, or saved before the
    field) counts its dead-feed time barrier from its entry bar, as before backlog 163, never from a close restored from
    the orders file (a later exit). 3 held, entry 10:00, dead at 12:16: 3 + 9 bars >= 10, closed."""
    pb, pid, last = _counted_trade(tmp_path)
    saved = _orders(tmp_path)
    del saved["open"][str(pid)]["bars_through"]
    (tmp_path / "orders_icm-demo.json").write_text(json.dumps(saved))
    eng = _engine(tmp_path, pb)
    assert eng._closed_through == last                       # restored, but not counted by this engine
    eng.open[pid].max_bars = 10
    now = last + 2 * M15 + pd.Timedelta(minutes=1)
    eng._quote_invalid_since = now - pd.Timedelta(seconds=QUOTE_DEAD_S)
    eng._host_clock_exits(now)
    assert pid not in eng.open


def test_a_saved_close_ahead_of_the_host_clock_is_ignored_and_journalled(tmp_path):
    """Trading-safety review S-3: a close later than the host clock plus one decision bar (a broker clock that ran
    ahead) would stop every close until the clock caught up; it is ignored and journalled."""
    pb, _, _ = _counted_trade(tmp_path)
    saved = _orders(tmp_path)
    ahead = (pd.Timestamp.now("UTC") + pd.Timedelta(days=1)).floor("15min")
    saved["closed_through"] = ahead.isoformat()
    (tmp_path / "orders_icm-demo.json").write_text(json.dumps(saved))
    eng = _engine(tmp_path, pb)
    assert eng._closed_through is None
    [row] = [d for d in eng.decisions if d.get("action") == "restart_closed_through_ignored"]
    assert row["close"] == ahead.isoformat()
    assert not [d for d in eng.decisions if d.get("action") == "restart_closed_through"]


def test_a_failed_save_at_a_close_blocks_entries_while_the_exits_still_run(tmp_path):
    """Backlog 163: the close is saved before its decisions (`downtime.processed`). A save that raises is a tick error:
    entries are refused at that close (`tick_error`), while the stop check, the close retries and the close's own
    position management still run."""
    pb, pid, last = _counted_trade(tmp_path)
    eng = _engine(tmp_path, pb)
    _feed(eng, pb, last + pd.Timedelta(minutes=1), 14)       # up to 1h59: the 2h00 bar is not closed yet
    calls: list[str] = []
    save, stop, retry = eng.state_store.save_orders, eng._stop_check, eng._retry_closes
    fails = [True]

    def failing_save(s: OrdersSnapshot) -> None:
        if fails.pop() if fails else False:
            raise OSError("disk full")
        save(s)

    def stop_check(t: Tick) -> None:
        calls.append("stop")
        stop(t)

    def retry_closes(now: pd.Timestamp) -> None:
        calls.append("retry")
        retry(now)
    eng.state_store.save_orders = failing_save  # type: ignore[method-assign]
    eng._stop_check = stop_check  # type: ignore[method-assign]
    eng._retry_closes = retry_closes  # type: ignore[method-assign]
    assert not eng.state.tick_error
    _feed(eng, pb, last + M15, 1)                            # closes 2h00; its save raises
    assert eng._closed_through == last + M15
    assert eng.state.tick_error and "tick_error" in eng.gate.account_reasons(eng.state)
    assert calls[:2] == ["stop", "retry"]
    assert eng.open[pid].bars_held == 4                      # the close's position management ran
