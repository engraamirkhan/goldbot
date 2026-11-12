"""Backlog 161: base bars that close while the engine is down count toward open trades' holds.

Design rule (Engine.on_bar_close): "position management stays on the base clock". A trade's label counts every bar of
the market, so a close missed while the engine was down (a restart, a deploy, a crash) still counts: at the first base
close after a restart each open trade is credited the closes since `bars_through` (the last close counted in its
`bars_held`, saved in the same orders-file record) whose bar the market was open for (`SessionTable.expected_open`), and
a close already counted before the restart is never counted again. Exits only get earlier, never later."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from goldbot.data.calendar import SessionTable
from goldbot.engine import Engine, EngineConfig, downtime
from goldbot.engine.runner import QUOTE_DEAD_S
from goldbot.engine.shadow import ShadowBook
from goldbot.execution.broker import Tick
from goldbot.execution.paper import PaperBroker
from goldbot.labels.exit_policy import ExitPolicy
from goldbot.specialists import SPECIALISTS
from goldbot.telegram.approvals import ApprovalCenter, Proposal

OWNER = 111
T0 = pd.Timestamp("2025-03-05 10:00", tz="UTC")       # a Wednesday
M15 = pd.Timedelta(minutes=15)
SEC = 900


def _tick(t: pd.Timestamp, bid: float = 2400.0) -> Tick:
    return Tick(ts_utc=t, bid=bid, ask=bid + 0.2)


def _engine(tmp_path: Path, pb: PaperBroker) -> Engine:
    return Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path), owner_user_id=OWNER),
                  pb, [], {}, ApprovalCenter({OWNER}))


def _feed(eng: Engine, pb: PaperBroker, start: pd.Timestamp, minutes: int) -> pd.Timestamp:
    """One tick a minute for `minutes` minutes from `start`; returns the time after the last one."""
    t = start
    for _ in range(minutes):
        pb.on_tick(_tick(t))
        eng.on_tick(_tick(t))
        t += pd.Timedelta(minutes=1)
    return t


def _counted_trade(tmp_path: Path) -> tuple[PaperBroker, int, pd.Timestamp]:
    """A filled trade whose engine counted the base closes T0+1h15 .. T0+1h45 (3 bars), then died after the 1h45 close
    with the trade saved. Returns the broker, the position id and the last counted close."""
    pb = PaperBroker(equity=10_000)
    pb.on_tick(_tick(T0))
    eng = _engine(tmp_path, pb)
    prop = Proposal(proposal_id="icm-demo-161-a", account_id="icm-demo", agent_id=SPECIALISTS["session_open"]().agent_id,
                    side=1, lots=0.1, entry=2400.2, stop=2390.0, target=2420.0, p=0.62, ev_r=0.2, spread_points=20,
                    top_features=[])
    eng._execute(prop, SPECIALISTS["session_open"](), 0.1, 2390.0, 2420.0, requested=2400.2)
    [pos] = pb.positions()
    eng.open[pos.position_id].max_bars = 10_000
    _feed(eng, pb, T0 + pd.Timedelta(hours=1), 3 * 15 + 1)       # the tick at 1h45 closes the third bar
    last = T0 + pd.Timedelta(hours=1, minutes=45)
    tr = eng.open[pos.position_id]
    assert tr.bars_held == 3 and tr.bars_through == last
    eng._save_orders()
    return pb, pos.position_id, last


def _saved(tmp_path: Path, pid: int) -> dict:
    return json.loads((tmp_path / "orders_icm-demo.json").read_text())["open"][str(pid)]


def test_a_downtime_of_n_base_bars_is_credited_at_the_first_close_after_the_restart(tmp_path):
    pb, pid, last = _counted_trade(tmp_path)
    eng = _engine(tmp_path, pb)                                       # down for the closes 2h00 .. 2h45 (4 bars)
    restart = last + 4 * M15
    _feed(eng, pb, restart, 15)
    assert eng.open[pid].bars_held == 3                               # nothing counted before the first close
    _feed(eng, pb, restart + M15, 1)                                  # closes restart+15m
    assert eng.open[pid].bars_held == 3 + 4 + 1
    assert eng.open[pid].bars_through == restart + M15
    assert _saved(tmp_path, pid)["bars_held"] == 8                    # on disk at that close (below the floor)
    _feed(eng, pb, restart + M15 + pd.Timedelta(minutes=1), 15)       # the next close: one more, no second credit
    assert eng.open[pid].bars_held == 9


def test_a_time_exit_the_downtime_makes_due_fires_at_the_first_close_after_the_restart(tmp_path):
    pb, pid, last = _counted_trade(tmp_path)
    saved = json.loads((tmp_path / "orders_icm-demo.json").read_text())
    saved["open"][str(pid)]["max_bars"] = 3 + 6                       # 6 bars to go when the engine died
    (tmp_path / "orders_icm-demo.json").write_text(json.dumps(saved))
    eng = _engine(tmp_path, pb)
    restart = last + 8 * M15                                          # 8 closes missed
    _feed(eng, pb, restart, 15)
    assert pid in eng.open
    _feed(eng, pb, restart + M15, 1)                                  # the first close: 3 + 8 + 1 >= 9
    assert pid not in eng.open and pid not in {p.position_id for p in pb.positions()}
    assert any(d.get("action") == "time_exit" and d.get("position") == pid and d["ok"] for d in eng.decisions)


def test_a_restart_whose_first_quote_is_stamped_before_the_last_counted_close_never_counts_it_again(tmp_path):
    pb, pid, last = _counted_trade(tmp_path)
    eng = _engine(tmp_path, pb)
    _feed(eng, pb, last - pd.Timedelta(minutes=2), 2)                 # the terminal's stale quote, then one at 1h44
    _feed(eng, pb, last, 1)                                           # re-closes the 1h45 bar the dead engine counted
    assert eng.open[pid].bars_held == 3 and eng.open[pid].bars_through == last
    _feed(eng, pb, last + pd.Timedelta(minutes=1), 15)                # closes 2h00: counted once, nothing missed
    assert eng.open[pid].bars_held == 4


def test_an_orders_file_saved_before_bars_through_counts_from_the_next_close_as_before(tmp_path):
    """The first deploy: a trade saved by the previous release has no `bars_through`; it is never credited a guess."""
    pb, pid, last = _counted_trade(tmp_path)
    saved = json.loads((tmp_path / "orders_icm-demo.json").read_text())
    del saved["open"][str(pid)]["bars_through"]
    (tmp_path / "orders_icm-demo.json").write_text(json.dumps(saved))
    eng = _engine(tmp_path, pb)
    restart = last + 4 * M15
    _feed(eng, pb, restart, 16)
    assert eng.open[pid].bars_held == 3 + 1 and eng.open[pid].bars_through == restart + M15
    _feed(eng, pb, restart + M15 + pd.Timedelta(minutes=1), 15)
    assert eng.open[pid].bars_held == 5


def test_two_restarts_in_a_row_credit_each_downtime_once(tmp_path):
    pb, pid, last = _counted_trade(tmp_path)
    _engine(tmp_path, pb)                                             # starts and dies before any close
    second = _engine(tmp_path, pb)
    assert second.open[pid].bars_held == 3 and second.open[pid].bars_through == last
    restart = last + 4 * M15
    _feed(second, pb, restart, 16)                                    # 4 missed + its first close
    assert second.open[pid].bars_held == 8
    third = _engine(tmp_path, pb)                                     # dies again after that close
    assert third.open[pid].bars_held == 8 and third.open[pid].bars_through == restart + M15
    again = restart + M15 + 3 * M15                                   # 3 more closes missed, the first 4 never again
    _feed(third, pb, again, 16)
    assert third.open[pid].bars_held == 8 + 3 + 1


def test_a_trade_never_counted_is_written_to_the_orders_file_as_before(tmp_path):
    """Additive (ADR 0010): `bars_through` is written only once a close counted, so an untouched file reads as before."""
    pb = PaperBroker(equity=10_000)
    pb.on_tick(_tick(T0))
    eng = _engine(tmp_path, pb)
    prop = Proposal(proposal_id="icm-demo-161-b", account_id="icm-demo", agent_id=SPECIALISTS["session_open"]().agent_id,
                    side=1, lots=0.1, entry=2400.2, stop=2390.0, target=2420.0, p=0.62, ev_r=0.2, spread_points=20,
                    top_features=[])
    eng._execute(prop, SPECIALISTS["session_open"](), 0.1, 2390.0, 2420.0, requested=2400.2)
    [pos] = pb.positions()
    assert "bars_through" not in _saved(tmp_path, pos.position_id)


def test_a_hard_flat_whose_policy_close_was_missed_fires_at_the_first_close_after_a_restart(tmp_path):
    """Safety review S-b: the label flattens at the first close of the policy timeframe at or after the deadline. That
    close (13:00 for a 12:15 deadline on 1h) fell inside the downtime (counted through 11:45), so the first base
    close after the restart (13:15, off the 1h grid) is the earliest the engine can flatten, above the floor too."""
    pb, pid, last = _counted_trade(tmp_path)
    eng = _engine(tmp_path, pb)
    tr = eng.open[pid]
    tr.policy, tr.timeframe = ExitPolicy(trail_atr=1.0, flat_before_min=30), "1h"
    tr.flat_at = last + 2 * M15                                       # passed while the engine was down
    live = {p.position_id for p in pb.positions()}
    eng._closed_through = last + 6 * M15                              # its first close: 13:15, off the 1h grid
    downtime.age(eng, live)
    assert pid not in eng.open
    assert [d["action"] for d in eng.decisions if d.get("position") == pid] == ["hard_flat"]


def test_a_downtime_over_a_weekend_counts_only_the_bars_the_market_was_open_for():
    """Friday 21:00 UTC close to Monday 01:15 UTC close (server Europe/Athens, UTC+2 in March): Friday's bars from
    21:00 to the 21:57 close (21:00, 21:15, 21:30, 21:45: 4) and Sunday's from the 23:02 reopen to Monday 01:00
    (23:00 .. 00:45: 8). The weekend never counts."""
    fri, mon = pd.Timestamp("2025-03-07 21:00", tz="UTC"), pd.Timestamp("2025-03-10 01:15", tz="UTC")
    assert downtime.missed_closes(SessionTable(), SEC, fri, mon) == 4 + 8


def test_a_downtime_over_an_exchange_holiday_never_counts_the_holiday():
    """Good Friday 2025 (CME metals closed): Thursday 2025-04-17 20:00 UTC close to Monday 00:15 UTC close (UTC+3 in
    April): Thursday's bars to the 20:59 daily break (20:00 .. 20:45: 4), nothing on Friday, Sunday's from the 22:02
    reopen (22:00 .. 23:45: 8). The plain session table would have counted Friday's hours."""
    thu, mon = pd.Timestamp("2025-04-17 20:00", tz="UTC"), pd.Timestamp("2025-04-21 00:15", tz="UTC")
    assert downtime.missed_closes(SessionTable(), SEC, thu, mon) == 4 + 8


def test_no_close_is_missed_between_two_consecutive_closes_or_backwards():
    t = pd.Timestamp("2025-03-05 10:00", tz="UTC")
    assert downtime.missed_closes(SessionTable(), SEC, t, t + M15) == 0
    assert downtime.missed_closes(SessionTable(), SEC, t, t) == 0
    assert downtime.missed_closes(SessionTable(), SEC, t + M15, t) == 0


def test_a_session_table_that_cannot_answer_credits_nothing(caplog):
    """Fail safe: no credit is the behaviour before backlog 161 (the exit later, never a guessed earlier one)."""

    class Broken:
        def expected_open(self, utc_ts: pd.DatetimeIndex) -> None:
            raise ValueError("no table")

    t = pd.Timestamp("2025-03-05 10:00", tz="UTC")
    assert downtime.missed_closes(Broken(), SEC, t, t + 10 * M15) == 0      # type: ignore[arg-type]
    assert "credited nothing" in caplog.text


def test_a_hard_flat_whose_policy_close_is_still_ahead_is_left_to_its_timeframes_close(tmp_path):
    """Safety review S-b: a 1h policy with its deadline at 12:10, the engine down 12:05-12:12: the label flattens at
    13:00, which was not missed, so the first base close after the restart (12:15) does not flatten it."""
    pb, pid, last = _counted_trade(tmp_path)                          # counted through 11:45
    eng = _engine(tmp_path, pb)
    tr = eng.open[pid]
    tr.policy, tr.timeframe = ExitPolicy(trail_atr=1.0, flat_before_min=30), "1h"
    tr.flat_at = last + pd.Timedelta(minutes=25)                      # 12:10
    eng._closed_through = last + 2 * M15                              # its first close: 12:15
    downtime.age(eng, {p.position_id for p in pb.positions()})
    assert pid in eng.open
    assert not [d for d in eng.decisions if d.get("position") == pid]


def test_the_credit_of_a_downtime_is_capped_at_catch_up_days():
    """A stale orders file (months old) never credits more than CATCH_UP_DAYS of bars; an always-open table makes
    every bar count, so the cap is exactly what is credited."""
    table = SessionTable(always_open=True)
    t = pd.Timestamp("2025-01-01", tz="UTC")
    capped = downtime.CATCH_UP_DAYS * 86400 // SEC
    assert downtime.missed_closes(table, SEC, t, t + pd.Timedelta(days=120)) == capped
    assert downtime.missed_closes(table, SEC, t, t + pd.Timedelta(days=10)) == 10 * 96 - 1


def test_the_bars_credited_are_logged_and_kept_on_the_trade_through_to_its_closed_record(tmp_path, caplog):
    """Quant review Q-2: `downtime_bars` marks a trade whose exit may come at the restart close rather than at the
    label's deadline, so the label-vs-live reconciliation can exclude it; it survives a restart and the close."""
    pb, pid, last = _counted_trade(tmp_path)
    eng = _engine(tmp_path, pb)
    restart = last + 4 * M15
    caplog.set_level("INFO", logger="goldbot.engine.downtime")
    _feed(eng, pb, restart, 16)
    assert eng.open[pid].downtime_bars == 4 and _saved(tmp_path, pid)["downtime_bars"] == 4
    assert f"position {pid} credited 4 base bar(s)" in caplog.text
    again = _engine(tmp_path, pb)
    assert again.open[pid].downtime_bars == 4
    again.open[pid].max_bars = again.open[pid].bars_held + 1
    _feed(again, pb, restart + M15 + pd.Timedelta(minutes=1), 15)    # its time exit
    assert pid not in again.open
    [rec] = [json.loads(x) for x in (tmp_path / "closed_trades_icm-demo.jsonl").read_text().splitlines()]
    assert rec["exit_reason"] == "time_exit" and rec["downtime_bars"] == 4


def test_a_trade_with_no_downtime_records_no_downtime_bars(tmp_path):
    pb, pid, _ = _counted_trade(tmp_path)
    assert "downtime_bars" not in _saved(tmp_path, pid)


def test_the_no_quote_time_barrier_after_a_restart_counts_from_the_last_counted_close_not_the_entry(tmp_path):
    """Safety review item 6: right after a restart `_closed_through` is unset, and counting the dead-feed time barrier
    from the entry bar re-counted the closes already in `bars_held` (an early time exit). It counts from
    `bars_through`: 3 counted through 11:45, plus 12:00 and 12:15 by 12:16, is 5 of 6."""
    pb, pid, last = _counted_trade(tmp_path)
    eng = _engine(tmp_path, pb)
    eng.open[pid].max_bars = 6
    now = last + 2 * M15 + pd.Timedelta(minutes=1)
    eng._quote_invalid_since = now - pd.Timedelta(seconds=QUOTE_DEAD_S)
    eng._host_clock_exits(now)
    assert pid in eng.open
    later = now + M15                                                 # 12:30 closed too: 6 of 6
    eng._host_clock_exits(later)
    assert pid not in eng.open


def test_shadow_trades_open_across_an_engine_restart_without_a_resume_point_are_tagged_restart_gap(tmp_path):
    """Quant review Q-1: a trade open across a restart whose missed bars cannot be replayed (backlog 165: saved before
    `last_bar_ts`, after it advanced) is tagged (persisted) for calibration and shadow-vs-live reads to exclude; a
    reader that only opens the book (API, jobs) tags nothing. tests/test_shadow_restart_replay.py: the replay."""
    book = ShadowBook(tmp_path)
    t0 = pd.Timestamp("2025-03-05 10:00", tz="UTC")
    book.track("v1", t0)
    t = book.open_trade(version="v1", agent_id="a", side=1, bar_ts=t0, entry=2400.0, atr_usd=2.0, target_atr=1.5,
                        stop_atr=1.0, max_bars=4, p=0.6)
    assert t is not None
    t.bars_held = 2                                                        # advanced, no last bar known
    book.save(t0)
    assert not ShadowBook(tmp_path).books["v1"].open[0].restart_gap        # a reader
    host = ShadowBook(tmp_path, host=True)                                 # the engine's own start
    assert host.books["v1"].open[0].restart_gap
    host.save(t0)
    assert ShadowBook(tmp_path).books["v1"].open[0].restart_gap            # persisted
