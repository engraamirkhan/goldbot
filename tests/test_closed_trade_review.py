"""Closed-trade review (trading-safety findings): no fake closes on a terminal fault, the unreadable-book check and its
alert, a guarded kill switch and weekend rule that retry until done, close-confirmed bookkeeping, backoff only when the
market is closed, and a closed-trade record that is retried rather than dropped."""
import json
import os
from collections import namedtuple
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.config import load_settings
from goldbot.engine import Engine, runner
from goldbot.execution import mt5_adapter
from goldbot.execution.broker import OrderResult, Tick
from goldbot.execution.paper import PaperBroker
from goldbot.ops import gates_phase, health
from goldbot.ops.gates_phase import load_closed_trades
from goldbot.ops.health import AccountRef, HealthContext, HealthReport, HealthWatch
from goldbot.risk.gate import Stage
from tests.test_closed_trades import (
    COMM,
    SESSION,
    T0,
    TREND,
    _engine,
    _enter,
    _lines,
    _records,
    _settle,
    _tick,
)
from tests.test_margin_and_tick_dedup import _adapter, _fake_mt5


def _down(*_: Any, **__: Any) -> Any:
    raise RuntimeError("positions_get failed: (-10004, 'No IPC connection')")


def _refuse(retcode: int) -> OrderResult:
    return OrderResult(ok=False, retcode=retcode, order_id=None, position_id=None, filled_lots=0.0, price=None,
                       message="refused")


class _FlakyClose:
    """PaperBroker.close that fails for the positions in `bad` (raising, or refused with a retcode) until healed."""

    def __init__(self, pb: PaperBroker, bad: set[int], retcode: int | None = None):
        self.real, self.bad, self.retcode = pb.close, set(bad), retcode
        self.calls: list[int] = []

    def __call__(self, position_id: int, lots: float | None = None) -> OrderResult:
        self.calls.append(position_id)
        if position_id in self.bad:
            if self.retcode is None:
                raise RuntimeError("terminal: order_send failed")
            return _refuse(self.retcode)
        return self.real(position_id, lots)


def _state(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "engine_icm-demo.json").read_text())


# ---------------------------------------------------------------------------------------------- the MT5 adapter
MPos = namedtuple("MPos", "ticket symbol type volume price_open sl tp magic comment time_msc profit")


def test_mt5_positions_raise_on_a_terminal_error_instead_of_reading_as_a_flat_book(monkeypatch):
    b = _adapter(monkeypatch, _fake_mt5(positions_get=lambda **k: None, last_error=lambda: (-10004, "No IPC"),
                                        POSITION_TYPE_BUY=0))
    with pytest.raises(RuntimeError, match="No IPC"):
        b.positions()
    monkeypatch.setattr(mt5_adapter.mt5, "positions_get", lambda **k: ())
    assert b.positions() == []                                              # a real flat book is still empty


def test_mt5_deal_history_raises_on_a_terminal_error_and_an_empty_history_is_empty(monkeypatch):
    b = _adapter(monkeypatch, _fake_mt5(history_deals_get=lambda a, z: None, last_error=lambda: (-1, "history")))
    with pytest.raises(RuntimeError, match="history"):
        b.deals_since(T0)
    monkeypatch.setattr(mt5_adapter.mt5, "history_deals_get", lambda a, z: ())
    assert b.deals_since(T0).empty


def test_mt5_close_and_modify_fail_loudly_on_a_terminal_error_never_as_no_such_position(monkeypatch):
    pos = MPos(7, "XAUUSD", 0, 0.1, 2400.0, 2390.0, 2420.0, 260150, "c", 1_741_168_800_000, 0.0)
    b = _adapter(monkeypatch, _fake_mt5(positions_get=lambda **k: None, POSITION_TYPE_BUY=0, TRADE_ACTION_DEAL=1,
                                        TRADE_ACTION_SLTP=6, ORDER_FILLING_IOC=1, order_send=lambda req: None))
    with pytest.raises(RuntimeError, match="positions_get failed"):
        b.close(7)
    with pytest.raises(RuntimeError, match="positions_get failed"):
        b.modify(7, 2395.0, None)
    monkeypatch.setattr(mt5_adapter.mt5, "positions_get", lambda **k: (pos,))
    monkeypatch.setattr(b, "_filling", lambda: 1)
    r = b.close(7)                                                          # order_send None: refused, not a crash
    assert not r.ok and r.retcode == -1 and "order_send failed" in r.message
    assert not b.modify(7, 2395.0, None).ok


# ---------------------------------------------------------------------------------------------- 1. no fake closes
def test_a_terminal_returning_none_records_no_close_and_keeps_open_trades_then_the_real_exit_is_recorded(
        tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path, TREND)
    pid = _enter(eng, pb, TREND)
    real_pos, real_deals = pb.positions, pb.deals_since
    monkeypatch.setattr(pb, "positions", _down)
    monkeypatch.setattr(pb, "deals_since", _down)
    for k in range(1, eng.cfg.close_confirm_checks + 3):
        eng.on_tick(_tick(k, 2000.2))
        _settle(eng)
    assert pid in eng.open and _records(tmp_path) == [] and pb.positions is not real_pos
    # a reply that drops the trade while the deal history cannot be read: still never a close
    monkeypatch.setattr(pb, "positions", lambda magic_prefix=None: [])
    for _ in range(eng.cfg.close_confirm_checks + 2):
        _settle(eng)
    assert pid in eng.open and _records(tmp_path) == []
    monkeypatch.setattr(pb, "positions", real_pos)
    monkeypatch.setattr(pb, "deals_since", real_deals)
    pb.on_tick(_tick(30, 2000.0 + TREND.label_spec.target_atr))           # the real exit: the server takes the target
    _settle(eng)
    (t,) = _records(tmp_path)
    assert t.position_id == pid and t.exit_reason == "target" and pid not in eng.open


def test_an_empty_deal_history_is_not_a_confirmed_close(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path, TREND)
    pid = _enter(eng, pb, TREND)
    monkeypatch.setattr(pb, "positions", lambda magic_prefix=None: [])
    monkeypatch.setattr(pb, "deals_since", lambda since: pd.DataFrame())
    for _ in range(eng.cfg.close_confirm_checks - 1):
        _settle(eng)
    assert pid in eng.open and _records(tmp_path) == []


# ---------------------------------------------------------------------------------------------- 2. unreadable book
def test_failed_broker_reads_set_positions_unreadable_block_entries_and_clear_when_it_reads_again(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path, TREND, state_every_s=1)
    _enter(eng, pb, TREND)
    real = pb.positions
    monkeypatch.setattr(pb, "positions", _down)
    for k in range(3):
        eng.on_tick(_tick(1 + k, 2000.2))                                 # several reads per tick count once
    st = _state(tmp_path)
    assert st["positions_unreadable"] == 3 and "positions_unreadable" in st["dq_checks"] and st["dq_error"]
    assert eng.state.dq_error and "data_quality_error" in eng.gate.check(_intent(), eng.state).reasons
    assert sum(d["action"] == "book_unreadable" for d in eng.decisions) == 1
    monkeypatch.setattr(pb, "positions", real)
    eng.on_tick(_tick(5, 2000.2))
    st = _state(tmp_path)
    assert st["positions_unreadable"] == 0 and "positions_unreadable" not in st["dq_checks"]


def test_a_failing_deal_history_counts_as_unreadable_until_no_close_waits_on_it(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path, TREND)
    pid = _enter(eng, pb, TREND)
    real = pb.positions
    monkeypatch.setattr(pb, "positions", lambda magic_prefix=None: [])
    monkeypatch.setattr(pb, "deals_since", _down)
    _settle(eng)
    assert eng._book_unreadable() == 1 and pid in eng.open
    monkeypatch.setattr(pb, "positions", real)                             # the trade is listed again
    _settle(eng)
    assert eng._book_unreadable() == 0


def _intent() -> Any:
    from goldbot.risk import Intent
    return Intent(agent_id="trend-x", side=1, p=0.62, target_atr=1.5, stop_atr=1.0, atr_usd=4.0, cost_atr=0.1,
                  multiplier=1.0, price=2000.2)


def _hctx(tmp_path: Path) -> HealthContext:
    return HealthContext(state_dir=tmp_path, now=pd.Timestamp("2026-10-07 14:00", tz="UTC"), settings=load_settings(),
                         accounts=[AccountRef(account_id="icm-demo")], get_secret=lambda k: "x",
                         disk_usage=lambda p: (100e9, 50e9, 50e9))


def _engine_state(tmp_path: Path, **kw: Any) -> None:
    payload = {"account": "icm-demo", "ts": pd.Timestamp("2026-10-07 14:00", tz="UTC").timestamp() - 30,
               "stage": "normal", "last_tick_age_s": 0.5, "blackout": None, "dq_error": False, "dq_checks": []}
    payload.update(kw)
    (tmp_path / "engine_icm-demo.json").write_text(json.dumps(payload))


def test_health_warns_on_an_unreadable_book_and_alerts_after_n_failures_in_a_row(tmp_path):
    n = load_settings().risk.positions_unreadable_alert
    assert n == 5
    watch = HealthWatch(tmp_path)
    ctx = _hctx(tmp_path)
    for k in range(1, n + 1):
        _engine_state(tmp_path, dq_error=True, dq_checks=["positions_unreadable"], positions_unreadable=k)
        c = health.check_engine(ctx, "icm-demo")
        assert ("broker positions/deals unreadable" in c.reason and "data-quality error" not in c.reason)
        assert c.status == ("fail" if k >= n else "warn")
        text = watch.poll(HealthReport(ts=ctx.now, status=c.status, checks=[c]))
        assert (text is not None) == (k == n)                               # one Telegram alert, at the Nth failure
    assert text is not None and "unreadable 5 time(s) in a row" in text
    _engine_state(tmp_path, dq_error=True, dq_checks=["gap", "positions_unreadable"], positions_unreadable=1)
    c = health.check_engine(ctx, "icm-demo")
    assert c.status == "fail" and "data-quality error (gap)" in c.reason   # another error still fails at once


# ---------------------------------------------------------------------------------------------- 3. guarded kill switch
def test_a_raising_kill_switch_close_does_not_stop_the_others_and_is_retried_until_the_book_is_flat(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path, TREND)
    pids = [_enter(eng, pb, TREND, at=T0 + pd.Timedelta(seconds=k)) for k in range(3)]
    flaky = _FlakyClose(pb, {pids[1]})
    monkeypatch.setattr(pb, "close", flaky)
    eng.state.stage = Stage.HALTED
    pb.on_tick(_tick(10, 1999.5))
    assert eng._kill_switch(everything=True) is False
    eng._save_orders()
    assert sorted(t.position_id or 0 for t in _records(tmp_path)) == sorted([pids[0], pids[2]])
    assert pids[1] in eng.open and eng._kill_pending and pids[1] in eng._close_due
    eng.on_tick(_tick(11, 1999.5))                                         # still raising: tried again, kept
    assert flaky.calls.count(pids[1]) >= 2 and pids[1] in eng.open
    flaky.bad.clear()
    eng.on_tick(_tick(12, 1999.5))
    assert pids[1] not in eng.open and not pb.positions()
    eng.on_tick(_tick(13, 1999.5))
    assert not eng._kill_pending
    assert sorted(t.position_id or 0 for t in _records(tmp_path)) == sorted(pids)
    assert {t.exit_reason for t in _records(tmp_path)} == {"kill_switch_close"}


def test_the_kill_switch_runs_again_next_tick_when_the_book_cannot_be_read(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path, TREND)
    pid = _enter(eng, pb, TREND)
    real = pb.positions
    monkeypatch.setattr(pb, "positions", _down)
    eng.state.stage = Stage.HALTED
    assert eng._kill_switch(everything=True) is False and eng._kill_pending and pid in eng.open
    monkeypatch.setattr(pb, "positions", real)
    eng.on_tick(_tick(1, 1999.5))
    assert pid not in eng.open and not pb.positions()


def test_the_weekend_rule_is_done_only_when_every_loser_closed_and_a_failed_one_is_retried(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path, TREND)
    fri = pd.Timestamp("2025-03-07 19:00", tz="UTC")                       # 21:00 server; the cut is 21:30
    pids = [_enter(eng, pb, TREND, at=fri + pd.Timedelta(seconds=k)) for k in range(3)]
    flaky = _FlakyClose(pb, {pids[0]})
    monkeypatch.setattr(pb, "close", flaky)
    cut = Tick(ts_utc=fri + pd.Timedelta(minutes=31), bid=1999.8, ask=1999.8)
    pb.on_tick(cut)
    eng._server_clock(cut.ts_utc, cut)
    eng._save_orders()
    assert eng._weekend_done is None and pids[0] in eng.open
    assert sorted(t.position_id or 0 for t in _records(tmp_path)) == sorted(pids[1:])
    flaky.bad.clear()
    later = Tick(ts_utc=cut.ts_utc + pd.Timedelta(seconds=1), bid=1999.8, ask=1999.8)
    eng._retry_closes(later)                                               # the next tick re-sends the close
    eng._server_clock(later.ts_utc, later)                                 # the next refresh finds the rule complete
    assert pids[0] not in eng.open and eng._weekend_done == "2025-03-07"
    assert {t.exit_reason for t in _records(tmp_path)} == {"weekend_close_loser"} and _lines(tmp_path) == 3


# ---------------------------------------------------------------------------------------------- 4. close-confirmed
@pytest.mark.parametrize("path", ["kill_switch", "weekend", "time_exit"])
def test_a_refused_close_keeps_the_trade_and_records_it_only_when_a_retry_succeeds(tmp_path, monkeypatch, path):
    agent = SESSION if path == "time_exit" else TREND
    eng, pb = _engine(tmp_path, agent)
    fri = pd.Timestamp("2025-03-07 19:00", tz="UTC")
    pid = _enter(eng, pb, agent, at=fri)
    flaky = _FlakyClose(pb, {pid}, retcode=10004)                          # a requote: transient
    monkeypatch.setattr(pb, "close", flaky)
    tick = Tick(ts_utc=fri + pd.Timedelta(minutes=31), bid=1999.9, ask=1999.9)
    pb.on_tick(tick)
    eng._last_tick = tick
    if path == "kill_switch":
        eng._kill_switch(everything=True)
    elif path == "weekend":
        assert eng._weekend_rule(tick) is False
    else:
        for _ in range(agent.label_spec.max_bars + 1):
            eng._manage_open(pd.DataFrame())
    eng._save_orders()
    assert pid in eng.open and _records(tmp_path) == []
    flaky.bad.clear()
    eng._retry_closes(Tick(ts_utc=tick.ts_utc + pd.Timedelta(seconds=1), bid=1999.9, ask=1999.9))
    eng._save_orders()
    (t,) = _records(tmp_path)
    assert pid not in eng.open and t.exit_reason == {"kill_switch": "kill_switch_close", "weekend": "weekend_close_loser",
                                                       "time_exit": "time_exit"}[path]


def test_reconcile_keeps_the_initial_stop_and_lots_for_r_not_the_trailed_stop(tmp_path):
    eng, pb = _engine(tmp_path, TREND)
    pid = _enter(eng, pb, TREND)
    first = eng.open[pid]
    assert first.initial_sl is not None
    pb.modify(pid, 2000.5, None)                                           # trailed at the broker
    del eng.open[pid]                                                      # the open table lost the trade
    eng._reconcile(T0 + pd.Timedelta(minutes=1))
    tr = eng.open[pid]
    assert tr.initial_sl == first.initial_sl and tr.initial_lots == first.initial_lots and tr.sl == 2000.5
    assert tr.agent_id == TREND.agent_id and tr.client_order_id == first.client_order_id
    pb.on_tick(_tick(30, 2000.5))                                          # the trailed stop is hit
    _settle(eng)
    (t,) = _records(tmp_path)
    risk = abs(2000.0 - first.initial_sl) * 0.1 * 100
    assert t.r == pytest.approx(t.pnl / risk)


# ---------------------------------------------------------------------------------------------- 5. backoff
def test_a_transient_refusal_of_the_engine_stop_is_retried_every_tick(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path, TREND)
    pid = _enter(eng, pb, TREND)
    eng.open[pid].sl = 2000.5
    flaky = _FlakyClose(pb, {pid}, retcode=10021)                          # no quotes / requote
    monkeypatch.setattr(pb, "close", flaky)
    monkeypatch.setattr(pb, "modify", lambda *a, **k: _refuse(10021))      # the broker's stop stays loose
    for s in range(5):
        eng.on_tick(Tick(ts_utc=T0 + pd.Timedelta(seconds=60 + s), bid=2000.4, ask=2000.4))
    assert flaky.calls.count(pid) == 5 and pid not in eng._close_backoff  # once per tick, no backoff
    flaky.bad.clear()
    eng.on_tick(Tick(ts_utc=T0 + pd.Timedelta(seconds=66), bid=2000.6, ask=2000.6))   # retried though back above
    assert pid not in eng.open and _records(tmp_path)[0].exit_reason == "engine_stop_close"


@pytest.mark.parametrize("retcode,backs_off", [(10018, True), (10017, True), (10004, False), (10020, False)])
def test_the_kill_switch_backs_off_only_when_the_market_is_closed(tmp_path, monkeypatch, retcode, backs_off):
    eng, pb = _engine(tmp_path, TREND)
    pid = _enter(eng, pb, TREND)
    flaky = _FlakyClose(pb, {pid}, retcode=retcode)
    monkeypatch.setattr(pb, "close", flaky)
    eng.state.stage = Stage.HALTED
    t0 = T0 + pd.Timedelta(minutes=1)
    eng._last_tick = Tick(ts_utc=t0, bid=1999.5, ask=1999.5)
    eng._kill_switch(everything=True)
    for s in range(1, 11):
        eng.on_tick(Tick(ts_utc=t0 + pd.Timedelta(seconds=s), bid=1999.5, ask=1999.5))
    assert flaky.calls.count(pid) == (1 if backs_off else 11)
    assert (pid in eng._close_backoff) == backs_off and pid in eng.open


def test_a_transient_hard_flat_refusal_is_retried_on_the_next_tick_not_the_next_bar(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path, SESSION)
    pid = _enter(eng, pb, SESSION, at=pd.Timestamp("2025-03-03 11:00", tz="UTC"))
    flat = eng.open[pid].flat_at
    assert flat is not None
    flaky = _FlakyClose(pb, {pid}, retcode=10004)
    monkeypatch.setattr(pb, "close", flaky)
    eng._manage_policies("15m", pd.DataFrame(), flat)
    assert pid in eng.open and eng._close_due[pid][0] == "hard_flat"
    flaky.bad.clear()
    eng.on_tick(Tick(ts_utc=flat + pd.Timedelta(seconds=1), bid=2000.1, ask=2000.1))
    (t,) = _records(tmp_path)
    assert pid not in eng.open and t.exit_reason == "hard_flat"


# ---------------------------------------------------------------------------------------------- 6. record retry
def test_a_failed_record_write_stays_queued_survives_a_restart_and_is_written_once(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path, TREND)
    pid = _enter(eng, pb, TREND)
    real = runner.append_closed_trade

    def disk_full(*_: Any) -> None:
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(runner, "append_closed_trade", disk_full)
    eng.open[pid].sl = 2000.5
    eng.on_tick(_tick(1, 2000.4))
    assert pid not in eng.open and [c.position_id for c in eng._closes] == [pid] and _lines(tmp_path) == 0
    eng.on_tick(_tick(1.2, 2000.4))                                        # within reconcile_every_s: not retried
    assert sum(d["action"] == "closed_trade_record_failed" for d in eng.decisions) == 1
    again, _ = _engine(tmp_path, TREND, pb=pb)                             # a restart keeps the queued record
    assert [c.position_id for c in again._closes] == [pid]
    monkeypatch.setattr(runner, "append_closed_trade", real)
    again.on_tick(_tick(3, 2000.4))
    again.on_tick(_tick(4, 2000.4))
    (t,) = _records(tmp_path)
    assert t.position_id == pid and t.exit_reason == "engine_stop_close" and not again._closes


def test_a_record_that_keeps_failing_is_reported_lost_after_the_retry_limit(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path, TREND, closed_record_retries=3, state_every_s=1)
    pid = _enter(eng, pb, TREND)

    def disk_full(*_: Any) -> None:
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(runner, "append_closed_trade", disk_full)
    eng.open[pid].sl = 2000.5
    for k in range(1, 6):
        eng.on_tick(_tick(k, 2000.4))
    assert sum(d["action"] == "closed_trade_record_failed" for d in eng.decisions) == 3
    assert [d["position"] for d in eng.decisions if d["action"] == "closed_trade_record_lost"] == [pid]
    assert not eng._closes and _state(tmp_path)["closed_records_lost"] == [pid]
    _engine_state(tmp_path, closed_records_lost=[pid])
    c = health.check_engine(_hctx(tmp_path), "icm-demo")
    assert c.status == "fail" and "record LOST" in c.reason


# ---------------------------------------------------------------------------------------------- 7. short and torn writes
def test_a_short_write_is_completed(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path, TREND)
    _enter(eng, pb, TREND)
    real = os.write
    monkeypatch.setattr(gates_phase.os, "write", lambda fd, data: real(fd, bytes(data[:7])))   # 7 bytes at a time
    pb.on_tick(_tick(30, 2002.5))
    _settle(eng)
    assert len(_records(tmp_path)) == 1 and _lines(tmp_path) == 1


def test_a_torn_line_is_skipped_the_rest_still_count_and_the_engine_records_a_dq_warning(tmp_path):
    eng, pb = _engine(tmp_path, TREND)
    for k in range(2):
        _enter(eng, pb, TREND, at=T0 + pd.Timedelta(hours=k))
        pb.on_tick(Tick(ts_utc=T0 + pd.Timedelta(hours=k, minutes=30), bid=2002.5, ask=2002.5))
        _settle(eng)
    f = gates_phase.closed_trade_file(tmp_path, "icm-demo")
    first, second = f.read_text().splitlines()
    f.write_text(first + "\n" + second[: len(second) // 2] + "\n" + second + "\n")   # a torn line in the middle
    trades, err = load_closed_trades(tmp_path)
    assert len(trades) == 2 and err is not None and "1 unreadable line(s) skipped" in err
    again, _ = _engine(tmp_path, TREND, pb=pb, state_every_s=1)
    assert again._recorded == {t.position_id for t in trades}            # never built from a truncated list
    again.on_tick(_tick(200, 2002.5))
    st = _state(tmp_path)
    assert any(w.startswith("closed_trades_unreadable") for w in st["dq_warnings"]) and not st["dq_error"]
    f.write_text(f.read_text() + second[:20])                             # a torn tail without a newline
    gates_phase.append_closed_trade(tmp_path, trades[0].model_copy(update={"position_id": 999}))
    trades, err = load_closed_trades(tmp_path)
    assert {t.position_id for t in trades} >= {999} and len(trades) == 3
    assert COMM > 0 and isinstance(again, Engine)
