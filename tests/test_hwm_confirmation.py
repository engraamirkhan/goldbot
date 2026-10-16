"""BACKLOG 54 (ADR 0018): a rise in balance_closed_hwm is applied only once confirmed, by the deal history or by a
second balance read that agrees. One inflated balance read never raises the mark, so it can never cause a phantom
size-down or kill switch on the real reads after it; two agreeing reads, or a read the deal history explains, raise it
as before; a restart between the two reads never applies an unconfirmed rise."""
import json
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.config import load_settings
from goldbot.db.migrations import CORE, compat_back_to
from goldbot.engine import Engine, EngineConfig
from goldbot.execution.broker import AccountInfo, OrderIntent, Tick
from goldbot.execution.paper import PaperBroker
from goldbot.risk import hwm
from goldbot.risk.gate import RiskLimits, Stage
from goldbot.telegram.approvals import ApprovalCenter

T0 = pd.Timestamp("2025-03-05 10:01", tz="UTC")
TOL = 0.001


def _tick(t: pd.Timestamp, bid: float = 2400.0) -> Tick:
    return Tick(ts_utc=t, bid=bid, ask=bid + 0.2)


def _new_engine(tmp_path: Path, pb: PaperBroker) -> Engine:
    return Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path), owner_user_id=111,
                               halt_checks=False), pb, [], {}, ApprovalCenter({111}))


class _Terminal(PaperBroker):
    """A paper broker that does not declare its balance reads exact, as a real terminal (MT5, the bridge) does not."""

    balance_exact = False


def _engine(tmp_path: Path) -> tuple[Engine, PaperBroker]:
    pb = _Terminal(equity=10_000)
    pb.on_tick(_tick(T0))
    eng = _new_engine(tmp_path, pb)
    eng._refresh_account(_tick(T0))               # high-water mark 10,000, set at T0
    assert eng.state.balance_closed_hwm == 10_000
    return eng, pb


def _open(pb: PaperBroker, cid: str, lots: float = 0.1) -> int:
    r = pb.place_order(OrderIntent(client_order_id=cid, symbol="XAUUSD", side=1, lots=lots, sl=2300.0, tp=2500.0,
                                   magic=260150, comment=cid))
    assert r.ok and r.position_id is not None
    return r.position_id


class _Balances:
    """`account()` reads scripted by balance (None = the broker's own read). The equity moves with the balance, as a
    half-filled struct or a glitch would report it."""

    def __init__(self, pb: PaperBroker, *balances: float | None):
        self.real, self.queue = pb.account, list(balances)

    def __call__(self) -> AccountInfo:
        acc = self.real()
        b = self.queue.pop(0) if self.queue else None
        if b is None:
            return acc
        return acc.model_copy(update={"balance": b, "equity": acc.equity - acc.balance + b})


def _script(monkeypatch: Any, pb: PaperBroker, *balances: float | None) -> None:
    monkeypatch.setattr(pb, "account", _Balances(pb, *balances))


def _read(eng: Engine, s: float) -> None:
    eng._refresh_account(_tick(T0 + pd.Timedelta(seconds=s)))


def _actions(eng: Engine) -> list[str]:
    return [d["action"] for d in eng.decisions if str(d.get("action", "")).startswith("hwm_")]


def _deals(*rows: tuple[pd.Timestamp, int, float]) -> pd.DataFrame:
    """An MT5 deal history: (time, MT5 deal type, profit) per deal; commission -1 on trade deals."""
    return pd.DataFrame([{"ticket": i + 1, "ts_utc": ts, "type": t, "entry": 1 if t in (0, 1) else 0,
                          "position_id": 100 + i if t in (0, 1) else 0, "volume": 0.1 if t in (0, 1) else 0.0,
                          "price": 2400.0, "magic": 260150, "profit": p, "commission": -1.0 if t in (0, 1) else 0.0,
                          "swap": 0.0, "fee": 0.0} for i, (ts, t, p) in enumerate(rows)])


def _stage(eng: Engine) -> Stage:
    return Stage(eng.state.stage)


# ---------------------------------------------------------------------------------------------- one inflated read
@pytest.mark.parametrize("inflated", [11_000.0, 12_000.0, 1e9])
def test_a_single_inflated_balance_read_leaves_the_mark_unchanged(tmp_path, monkeypatch, inflated):
    eng, pb = _engine(tmp_path)
    _script(monkeypatch, pb, inflated)
    _read(eng, 1)
    assert eng.state.balance_closed_hwm == 10_000
    assert _actions(eng) == ["hwm_rise_unconfirmed"]


def test_a_single_inflated_read_causes_no_size_down_or_halt_on_the_next_real_reads(tmp_path, monkeypatch):
    """Before ADR 0018 the 12,000 read raised the mark for good: the real 10,000 reads were a 16.7% drawdown,
    size-down at once and, two reads later (ADR 0016), the kill switch closing everything at market."""
    eng, pb = _engine(tmp_path)
    pid = _open(pb, "a")
    _script(monkeypatch, pb, 12_000.0)
    for s in (1, 2, 12, 22, 32):
        _read(eng, s)
    assert eng.state.balance_closed_hwm == 10_000
    assert _stage(eng) == Stage.NORMAL and not eng._kill_pending and not eng.state.equity_unconfirmed
    assert [p.position_id for p in pb.positions()] == [pid]


def test_a_refused_rise_is_logged_and_recorded_as_a_decision(tmp_path, monkeypatch, caplog):
    eng, pb = _engine(tmp_path)
    _script(monkeypatch, pb, 12_000.0)
    _read(eng, 1)
    with caplog.at_level("WARNING"):
        _read(eng, 2)
    refused = [d for d in eng.decisions if d.get("action") == "hwm_rise_refused"]
    assert len(refused) == 1 and refused[0]["candidate"] == 12_000.0 and refused[0]["balance"] == 10_000.0
    assert refused[0]["mark"] == 10_000
    assert any("high-water" in r.getMessage() for r in caplog.records)


def test_two_disagreeing_inflated_reads_do_not_raise_the_mark(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    _script(monkeypatch, pb, 12_000.0, 15_000.0)
    _read(eng, 1)
    _read(eng, 2)
    assert eng.state.balance_closed_hwm == 10_000
    assert _actions(eng) == ["hwm_rise_unconfirmed", "hwm_rise_refused", "hwm_rise_unconfirmed"]


def test_the_mark_is_never_lowered_by_a_low_balance_read(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    _script(monkeypatch, pb, 0.0, 5_000.0, float("nan"))
    for s in (1, 2, 3):
        _read(eng, s)
    assert eng.state.balance_closed_hwm == 10_000


# ---------------------------------------------------------------------------------------------- confirmed rises
def test_two_agreeing_balance_reads_raise_the_mark(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    _script(monkeypatch, pb, 10_500.0, 10_500.0)
    _read(eng, 1)
    assert eng.state.balance_closed_hwm == 10_000
    _read(eng, 2)
    assert eng.state.balance_closed_hwm == 10_500
    raised = [d for d in eng.decisions if d.get("action") == "hwm_raised"]
    assert raised[-1]["by"] == "second_read" and raised[-1]["from"] == 10_000 and raised[-1]["to"] == 10_500


def test_two_reads_that_agree_within_the_tolerance_raise_the_mark_to_the_lower(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    _script(monkeypatch, pb, 10_505.0, 10_500.0)                     # 5 apart, tolerance 10 (0.1% of 10,000)
    _read(eng, 1)
    _read(eng, 2)
    assert eng.state.balance_closed_hwm == 10_500


def test_a_close_without_an_mt5_deal_history_raises_the_mark_on_the_second_read(tmp_path):
    """A terminal whose deal history cannot explain the rise (no MT5 columns): the next read confirms."""
    eng, pb = _engine(tmp_path)
    pid = _open(pb, "a", lots=1.0)
    pb.on_tick(_tick(T0 + pd.Timedelta(seconds=1), 2410.0))
    assert pb.close(pid).ok
    gain = pb.account().balance
    assert gain > 10_000
    _read(eng, 2)
    assert eng.state.balance_closed_hwm == 10_000
    _read(eng, 3)
    assert eng.state.balance_closed_hwm == pytest.approx(gain)


def test_a_rise_the_deal_history_explains_raises_the_mark_on_the_first_read(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    seen: list[pd.Timestamp] = []

    def deals_since(since: pd.Timestamp) -> pd.DataFrame:
        seen.append(since)
        return _deals((T0 + pd.Timedelta(minutes=5), 1, 501.0))       # a close: +501 profit, -1 commission
    monkeypatch.setattr(pb, "deals_since", deals_since)
    _script(monkeypatch, pb, 10_500.0)
    _read(eng, 600)
    assert eng.state.balance_closed_hwm == 10_500
    assert seen == [T0]                                                # the anchor: when the mark was set
    raised = [d for d in eng.decisions if d.get("action") == "hwm_raised"]
    assert raised[-1]["by"] == "deals" and raised[-1]["deposits"] == 0.0


def test_a_deposit_in_the_deal_history_raises_the_mark_and_is_recorded(tmp_path, monkeypatch):
    """ADR 0018: a deposit raises the mark (the drawdown is measured on the capital the account now holds)."""
    eng, pb = _engine(tmp_path)
    monkeypatch.setattr(pb, "deals_since", lambda since: _deals((T0 + pd.Timedelta(minutes=5), 2, 1_000.0)))
    _script(monkeypatch, pb, 11_000.0)
    _read(eng, 600)
    assert eng.state.balance_closed_hwm == 11_000
    raised = [d for d in eng.decisions if d.get("action") == "hwm_raised"]
    assert raised[-1]["by"] == "deals" and raised[-1]["deposits"] == 1_000.0


def test_a_deal_history_that_does_not_account_for_the_rise_leaves_it_unconfirmed(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    monkeypatch.setattr(pb, "deals_since", lambda since: _deals((T0 + pd.Timedelta(minutes=5), 1, 101.0)))
    _script(monkeypatch, pb, 12_000.0)                                 # +2,000 against +100 of deals
    _read(eng, 600)
    assert eng.state.balance_closed_hwm == 10_000


def test_an_empty_or_unreadable_deal_history_never_explains_a_rise(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    monkeypatch.setattr(pb, "deals_since", lambda since: _deals())
    _script(monkeypatch, pb, 10_005.0)                                 # within the tolerance, but no deal at all
    _read(eng, 1)
    assert eng.state.balance_closed_hwm == 10_000

    def broken(since: pd.Timestamp) -> pd.DataFrame:
        raise RuntimeError("history_deals_get failed")
    monkeypatch.setattr(pb, "deals_since", broken)
    _script(monkeypatch, pb, 11_000.0)
    _read(eng, 2)
    assert eng.state.balance_closed_hwm == 10_000


def test_credit_in_the_deal_history_does_not_explain_a_balance_rise(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    monkeypatch.setattr(pb, "deals_since", lambda since: _deals((T0 + pd.Timedelta(minutes=5), 3, 1_000.0)))
    _script(monkeypatch, pb, 11_000.0)
    _read(eng, 600)
    assert eng.state.balance_closed_hwm == 10_000


def test_an_unconfirmed_rise_is_read_again_on_the_next_tick(tmp_path, monkeypatch):
    """As ADR 0016: the confirmation costs one tick, not the state interval (state_every_s = 10 s)."""
    eng, pb = _engine(tmp_path)
    t = T0.floor("15min") + pd.Timedelta(minutes=1)
    eng.on_tick(_tick(t))
    _script(monkeypatch, pb, 10_500.0, 10_500.0)
    eng.on_tick(_tick(t + pd.Timedelta(seconds=11)))                   # the state interval is due: account read
    assert eng.state.balance_closed_hwm == 10_000
    eng.on_tick(_tick(t + pd.Timedelta(seconds=12)))                   # 1 s later: read again, confirmed
    assert eng.state.balance_closed_hwm == 10_500


# ---------------------------------------------------------------------------------------------- restarts and storage
def test_a_restart_between_the_two_reads_does_not_apply_an_unconfirmed_rise(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    _script(monkeypatch, pb, 12_000.0)
    _read(eng, 1)
    eng._save_risk_state()
    again = _new_engine(tmp_path, pb)
    assert again.state.balance_closed_hwm == 10_000
    again._refresh_account(_tick(T0 + pd.Timedelta(seconds=5)))       # the real read after the restart
    assert again.state.balance_closed_hwm == 10_000 and Stage(again.state.stage) == Stage.NORMAL
    assert [d["action"] for d in again.decisions if d.get("action", "").startswith("hwm_")] == ["hwm_rise_refused"]


def test_a_pending_rise_survives_a_restart_so_an_agreeing_read_after_it_confirms(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    _script(monkeypatch, pb, 10_500.0)
    _read(eng, 1)
    again = _new_engine(tmp_path, pb)
    _script(monkeypatch, pb, 10_500.0)
    again._refresh_account(_tick(T0 + pd.Timedelta(seconds=5)))
    assert again.state.balance_closed_hwm == 10_500


def test_the_confirmation_state_is_in_core_db_not_a_json_file(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    _script(monkeypatch, pb, 10_500.0)
    _read(eng, 1)
    eng._save_risk_state()
    risk = json.loads((tmp_path / "risk_icm-demo.json").read_text())
    assert not any("pending" in k and "stage2" not in k for k in risk)
    assert {p.name for p in tmp_path.glob("*.json")} <= {"risk_icm-demo.json", "engine_icm-demo.json"}
    with sqlite3.connect(tmp_path / "core.db") as conn:
        body = json.loads(conn.execute("SELECT body FROM hwm_confirmation WHERE account_id = 'icm-demo'").fetchone()[0])
    assert body["pending_balance"] == 10_500.0 and body["mark"] == 10_000.0


def test_a_row_for_another_mark_is_void(tmp_path, monkeypatch):
    """A re-arm, or an older release that raised the mark on one read after a rollback, leaves a row for a mark that
    is no longer the risk state's: its candidate and anchor are ignored, so a read agreeing with that old candidate
    does not raise the mark, and the deal history is not read against an anchor that no longer holds."""
    eng, pb = _engine(tmp_path)
    _script(monkeypatch, pb, 10_500.0)
    _read(eng, 1)
    eng._save_risk_state()
    path = tmp_path / "risk_icm-demo.json"
    risk = json.loads(path.read_text())
    path.write_text(json.dumps({**risk, "balance_closed_hwm": 10_200.0}))      # an older release raised it
    again = _new_engine(tmp_path, pb)
    reads: list[pd.Timestamp] = []
    def deals_since(since: pd.Timestamp) -> pd.DataFrame:
        reads.append(since)
        return _deals((T0, 1, 301.0))
    monkeypatch.setattr(pb, "deals_since", deals_since)
    _script(monkeypatch, pb, 10_500.0)
    again._refresh_account(_tick(T0 + pd.Timedelta(seconds=5)))
    assert again.state.balance_closed_hwm == 10_200 and reads == []


def _broken(*a: Any, **k: Any) -> Any:
    raise OSError("disk")


def test_an_unwritable_core_db_never_stops_the_refresh_and_never_raises_on_one_read(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    monkeypatch.setattr(hwm.Database, "write", _broken)
    _script(monkeypatch, pb, 12_000.0, None)
    _read(eng, 1)
    _read(eng, 2)
    assert eng.state.balance_closed_hwm == 10_000


def test_a_core_db_that_cannot_be_opened_never_stops_the_engine_and_never_raises_on_one_read(tmp_path, monkeypatch):
    monkeypatch.setattr(hwm.Database, "open", classmethod(_broken))
    eng, pb = _engine(tmp_path)
    assert eng._hwm_store.db is None
    _script(monkeypatch, pb, 12_000.0, None, 10_500.0, 10_500.0)
    for s in (1, 2, 3, 4):
        _read(eng, s)
    assert eng.state.balance_closed_hwm == 10_500                     # in-process confirmation still works


def test_core_db_is_opened_once_per_engine_and_writes_with_a_short_busy_timeout(tmp_path, monkeypatch):
    """Review L1: the save runs on the tick thread, so it never migrates or waits the default 5 s busy timeout; the
    open (and migration) at start waits the default timeout, so a lock at start-up does not lose core.db."""
    opened: list[Any] = []
    real = hwm.Database.open.__func__                                 # type: ignore[attr-defined]

    def counting(cls: Any, *a: Any, **k: Any) -> Any:
        opened.append(k.get("busy_timeout_ms"))
        return real(cls, *a, **k)
    monkeypatch.setattr(hwm.Database, "open", classmethod(counting))
    timeouts: list[int] = []
    real_connect = hwm.connect

    def connecting(*a: Any, **k: Any) -> Any:
        timeouts.append(k["busy_timeout_ms"])
        return real_connect(*a, **k)
    monkeypatch.setattr(hwm, "connect", connecting)
    eng, pb = _engine(tmp_path)
    _script(monkeypatch, pb, 12_000.0, None, 10_500.0, 10_500.0)
    for s in (1, 2, 3, 4):
        _read(eng, s)
    assert opened == [None]                                            # once, with the default busy timeout
    assert timeouts and set(timeouts) == {hwm.WRITE_BUSY_TIMEOUT_MS} and 250 <= hwm.WRITE_BUSY_TIMEOUT_MS <= 500


def test_a_failed_open_at_start_is_retried_on_the_next_save(tmp_path, monkeypatch):
    """Review LOW: a lock at start-up must not leave core.db unused for the whole run."""
    real = hwm.Database.open.__func__                                 # type: ignore[attr-defined]
    calls: list[int] = []

    def first_fails(cls: Any, *a: Any, **k: Any) -> Any:
        calls.append(1)
        if len(calls) == 1:
            raise OSError("database is locked")
        return real(cls, *a, **k)
    monkeypatch.setattr(hwm.Database, "open", classmethod(first_fails))
    store = hwm.HwmStore(tmp_path)
    assert store.db is None
    conf = hwm.HwmConfirmation(account_id="icm-demo", mark=10_000.0, mark_utc=T0)
    assert store.save(conf) and store.db is not None and len(calls) == 2
    assert hwm.HwmStore(tmp_path).load("icm-demo") == conf


# ---------------------------------------------------------------------------------------------- review fixes
def test_a_real_peak_followed_by_a_loss_still_raises_the_mark_to_the_peak(tmp_path, monkeypatch):
    """Review M1: mark 10,000; a read of 10,300 the deals since the mark do not explain becomes the candidate; a loss
    then reads 9,900. The deals since the candidate take it to 9,900, so the peak was real: the mark rises to 10,300
    instead of staying 3% low until the next new high."""
    eng, pb = _engine(tmp_path)
    loss = (T0 + pd.Timedelta(seconds=20), 1, -399.0)                 # -399 profit, -1 commission

    def deals_since(since: pd.Timestamp) -> pd.DataFrame:
        return _deals(*[d for d in [loss] if d[0] >= since])
    monkeypatch.setattr(pb, "deals_since", deals_since)
    _script(monkeypatch, pb, 10_300.0, 9_900.0)
    _read(eng, 10)
    assert eng.state.balance_closed_hwm == 10_000
    _read(eng, 30)
    assert eng.state.balance_closed_hwm == 10_300
    raised = [d for d in eng.decisions if d.get("action") == "hwm_raised"]
    assert raised[-1]["by"] == "candidate_deals" and "hwm_rise_refused" not in _actions(eng)
    assert eng._hwm.mark_utc == T0 + pd.Timedelta(seconds=10)          # anchored where the peak was read


def test_a_glitch_followed_by_a_lower_read_without_deals_is_still_refused(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    monkeypatch.setattr(pb, "deals_since", lambda since: _deals())
    _script(monkeypatch, pb, 12_000.0, 9_900.0)
    _read(eng, 10)
    _read(eng, 30)
    assert eng.state.balance_closed_hwm == 10_000 and _actions(eng)[-1] == "hwm_rise_refused"


def test_paper_broker_balance_reads_are_exact_so_one_read_raises_the_mark(tmp_path, monkeypatch):
    pb = PaperBroker(equity=10_000)
    pb.on_tick(_tick(T0))
    eng = _new_engine(tmp_path, pb)
    eng._refresh_account(_tick(T0))
    _script(monkeypatch, pb, 10_500.0)
    _read(eng, 1)
    assert eng.state.balance_closed_hwm == 10_500
    assert [d for d in eng.decisions if d.get("action") == "hwm_raised"][-1]["by"] == "exact"


def test_only_the_paper_broker_declares_exact_balance_reads_and_a_remote_pass_through_cannot():
    from goldbot.execution.bridge import RemoteBroker
    from goldbot.execution.mt5_adapter import MT5Broker
    assert PaperBroker.balance_exact is True
    for cls in (RemoteBroker, MT5Broker):
        assert getattr(cls, "balance_exact", False) is False


def test_jittering_reads_above_the_mark_keep_the_first_pending_time_so_health_warns(tmp_path, monkeypatch):
    """Review M2: each disagreeing candidate replaces the last, but the wait is measured from the first of them."""
    eng, pb = _engine(tmp_path)
    reads = [10_500.0 if i % 2 else 10_700.0 for i in range(40)]
    _script(monkeypatch, pb, *reads)
    for i in range(40):                                                # one read every 10 s: 6.5 min
        _read(eng, 1 + 10 * i)
    assert eng.state.balance_closed_hwm == 10_000
    assert eng._hwm.pending_since_utc == T0 + pd.Timedelta(seconds=1)
    eng._save_risk_state()
    stuck = _health(tmp_path, T0 + pd.Timedelta(seconds=392))
    assert stuck.status == "warn" and "balance rise has waited" in stuck.reason


def test_a_read_at_or_below_the_mark_clears_the_pending_time(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    _script(monkeypatch, pb, 10_500.0, 10_700.0, 10_000.0, 10_600.0)
    for s in (1, 2, 3, 4):
        _read(eng, s)
    assert eng._hwm.pending_since_utc == T0 + pd.Timedelta(seconds=4)


def test_a_candidate_older_than_ten_minutes_is_void_in_the_rule():
    """Review L2: an agreeing read 11 minutes after the candidate does not confirm it; it is a new candidate."""
    old = _conf(pending_balance=1_050.0, pending_since_utc=T0, candidate_utc=T0)
    s = hwm.step(old, 1_000.0, 1_050.0, T0 + pd.Timedelta(minutes=11), TOL, _none)
    assert s.outcome == "pending" and s.expired == 1_050.0 and s.mark == 1_000.0
    assert s.conf.pending_since_utc == T0 + pd.Timedelta(minutes=11)
    s = hwm.step(old, 1_000.0, 1_050.0, T0 + pd.Timedelta(minutes=9), TOL, _none)
    assert s.outcome == "raised"


def test_a_candidate_older_than_ten_minutes_is_void_on_load(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    _script(monkeypatch, pb, 10_500.0)
    _read(eng, 1)                                                      # candidate at T0 + 1 s (2025)
    eng._save_risk_state()
    live = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path), owner_user_id=111,
                               halt_checks=False, live_clock=True), pb, [], {}, ApprovalCenter({111}))
    assert live._hwm.pending_balance is None and not live._reread_hwm
    replay = _new_engine(tmp_path, pb)                                 # tick clock: judged on its first read instead
    assert replay._hwm.pending_balance == 10_500.0


def test_a_future_dated_candidate_is_void_so_an_agreeing_read_does_not_raise():
    """Review LOW: a candidate stamped two days ahead has a negative age; it must never live for ever."""
    ahead = T0 + pd.Timedelta(days=2)
    future = _conf(pending_balance=1_050.0, pending_since_utc=ahead, candidate_utc=ahead)
    s = hwm.step(future, 1_000.0, 1_050.0, T0, TOL, _none)
    assert s.outcome == "pending" and s.mark == 1_000.0 and s.expired == 1_050.0
    assert s.conf.candidate_utc == T0 and s.conf.pending_since_utc == T0


def test_a_legacy_row_without_candidate_time_uses_its_pending_time():
    legacy = _conf(pending_balance=1_050.0, pending_since_utc=T0)
    assert legacy.candidate_at() == T0
    assert hwm.step(legacy, 1_000.0, 1_050.0, T0 + pd.Timedelta(minutes=1), TOL, _none).outcome == "raised"


def test_a_candidate_the_deals_after_it_carry_to_a_higher_read_raises_the_mark_to_that_read():
    def deals(since: pd.Timestamp) -> pd.DataFrame:
        return _deals((T0 + pd.Timedelta(seconds=5), 1, 21.0)) if since <= T0 else _deals()
    c = hwm.HwmConfirmation(account_id="a", mark=1_000.0, pending_balance=1_030.0, pending_since_utc=T0,
                            candidate_utc=T0)                          # no mark anchor: only the candidate's deals
    s = hwm.step(c, 1_000.0, 1_050.0, T0 + pd.Timedelta(seconds=10), TOL, deals)
    assert s.outcome == "raised" and s.mark == 1_050.0 and s.by == "candidate_deals"


def test_the_hwm_table_is_an_additive_core_migration_an_older_release_still_opens():
    assert CORE[-1].name == "hwm_confirmation" and CORE[-1].additive
    assert compat_back_to(CORE, len(CORE)) == 1


def test_the_rise_tolerance_comes_from_settings():
    s = load_settings()
    assert s.risk.hwm_rise_tolerance == pytest.approx(TOL)
    assert RiskLimits.from_settings(s.risk, tiny_live=False).hwm_rise_tolerance == pytest.approx(TOL)


# ---------------------------------------------------------------------------------------------- the pure rule
def _conf(**kw: Any) -> hwm.HwmConfirmation:
    return hwm.HwmConfirmation(account_id="a", mark=1_000.0, mark_utc=T0, **kw)


def _none(since: pd.Timestamp) -> pd.DataFrame:
    return _deals()


def test_the_rule_never_lowers_the_mark_and_raises_only_to_the_lower_of_two_agreeing_reads():
    s = hwm.step(_conf(pending_balance=1_050.0, pending_since_utc=T0), 1_000.0, 1_050.5, T0, TOL, _none)
    assert s.outcome == "raised" and s.mark == 1_050.0 and s.by == "second_read"
    assert s.conf.pending_balance is None and s.conf.mark == 1_050.0 and s.conf.mark_utc == T0
    s = hwm.step(_conf(pending_balance=1_050.0, pending_since_utc=T0), 1_000.0, 900.0, T0, TOL, _none)
    assert s.outcome == "refused" and s.mark == 1_000.0 and s.dropped == 1_050.0
    s = hwm.step(_conf(pending_balance=1_050.0, pending_since_utc=T0), 1_000.0, 1_200.0, T0, TOL, _none)
    assert s.outcome == "pending" and s.mark == 1_000.0 and s.dropped == 1_050.0 and s.conf.pending_balance == 1_200.0
    assert s.conf.pending_since_utc == T0                              # the first of the run is kept (review M2)


def test_without_an_anchor_the_deal_history_is_not_read():
    def never(since: pd.Timestamp) -> pd.DataFrame:
        raise AssertionError("read")
    s = hwm.step(hwm.HwmConfirmation(account_id="a", mark=1_000.0), 1_000.0, 1_100.0, T0, TOL, never)
    assert s.outcome == "pending"


# ---------------------------------------------------------------------------------------------- health
def _health(tmp_path: Path, now: pd.Timestamp) -> Any:
    from goldbot.ops import health
    ctx = health.HealthContext(state_dir=tmp_path, now=now, settings=load_settings(), get_secret=lambda k: None)
    return health.check_risk_state(ctx, "icm-demo")


def test_health_warns_when_a_rise_has_waited_for_confirmation_in_market_hours(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    _script(monkeypatch, pb, 12_000.0)
    _read(eng, 1)
    eng._save_risk_state()
    fresh = _health(tmp_path, T0 + pd.Timedelta(minutes=2))
    assert fresh.status == "ok" and "balance rise" not in fresh.reason
    stuck = _health(tmp_path, T0 + pd.Timedelta(minutes=10))
    assert stuck.status == "warn" and "balance rise has waited" in stuck.reason
    assert "12000" not in stuck.reason and "12,000" not in stuck.reason          # no amounts in health reasons


def test_health_ignores_a_confirmation_row_for_another_mark(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    _script(monkeypatch, pb, 12_000.0)
    _read(eng, 1)
    eng.state.balance_closed_hwm = 10_200.0                            # a re-arm moved the mark
    eng._save_risk_state()
    assert _health(tmp_path, T0 + pd.Timedelta(minutes=10)).status == "ok"


def test_health_without_core_db_reads_nothing_and_creates_nothing(tmp_path):
    (tmp_path / "risk_icm-demo.json").write_text(json.dumps({"stage": "normal", "balance_closed_hwm": 10_000.0}))
    assert _health(tmp_path, T0).status == "ok"
    assert not (tmp_path / "core.db").exists()
